"""Observe → decide → act loop (Phase K — UC4).

The capability that makes the universal control demo-able: give Vee a small goal
("open TextEdit, type this note, save it as notes.txt") and it runs a supervised
loop — **observe** (UC2) → model **decides** the next single action → **execute**
(UC1/UC3 primitives) through the safety gate → **re-observe** → repeat — until the
goal is done, a hard step cap is hit, the user vetoes a step, or the kill switch
fires.

Supervision is foregrounded: every observe/decide/act beat streams to the process
panel, every mutating step goes through the confirm card + kill switch (so the
user sees and can veto each step), the step cap stops runaways, and a failed
action re-observes and lets the model adjust rather than looping blindly.

Pure orchestration: it drives the passed-in executor + model client and emits via
a caller-supplied `emit` callback — no direct WebSocket / process_events coupling.
"""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional

import perception

log = logging.getLogger("valet.loop")

# Hard ceiling on loop iterations — a runaway backstop, not a target.
_DEFAULT_MAX_STEPS = 8
# Bail after this many consecutive non-veto failures (can't make progress).
_MAX_CONSECUTIVE_FAILS = 2

_ACTIONS = ("click", "type", "key", "open_app", "done", "fail")

# ── Hybrid autonomy (risk tiers per step) ───────────────────────────────────
# In hands-off mode each decided step is classified before it runs:
#   "auto"    → run straight through the raw executor (no confirm card) so a
#               chain of clicks/navigation feels hands-off. Kill switch still
#               checked immediately before the act.
#   "confirm" → run through the gating executor (Tier-1 confirm card) — used for
#               anything destructive or money-moving.
#   "login"   → DON'T act. Hand the credential entry to the human, wait for the
#               login form to clear, then resume the chain (user's choice:
#               "hand off to you"). VALET never types secrets.
# Over-classifying toward confirm/login is safe; the cost is one extra tap.
_DESTRUCTIVE_HINTS = (
    "delete", "remove", "trash", "discard", "erase", "uninstall", "wipe",
    "destroy", "clear all", "empty trash", "permanently",
)
_PAYMENT_HINTS = (
    "pay ", "payment", "buy ", "purchase", "checkout", "check out", "place order",
    "subscribe", "billing", "card number", "cvv", "cvc", "complete order",
    "complete purchase", "confirm and pay", "submit payment", "upgrade plan",
)
# Secure-field role + label cues that mean "credentials go here".
_LOGIN_ROLE_HINTS = ("securetextfield", "secure text field")
_LOGIN_LABEL_HINTS = ("password", "passcode", "passphrase", "one-time code", "verification code")


def _find_element(observation: dict, ref: Optional[str]) -> dict:
    """The observed element dict for `ref`, or {} if not found."""
    if not ref:
        return {}
    for e in observation.get("elements", []) or []:
        if e.get("ref") == ref:
            return e
    return {}


def _classify_step(decision: dict, observation: dict) -> str:
    """Risk tier for one decided step: 'auto' | 'confirm' | 'login'.

    Looks at the decided action, the target element's role/label, and the model's
    own stated reason. Conservative: a credential field → 'login'; any destructive
    or payment cue → 'confirm'; everything else → 'auto'."""
    act = decision.get("action")
    el = _find_element(observation, decision.get("ref"))
    role = (el.get("role") or "").lower()
    label = " ".join(filter(None, [
        el.get("title"), el.get("value"), role,
        decision.get("reason"), decision.get("combo"), decision.get("target"),
    ])).lower()

    # Credentials — clicking into or typing a secure field hands off to the human.
    if act in ("click", "type"):
        if any(h in role for h in _LOGIN_ROLE_HINTS) or any(h in label for h in _LOGIN_LABEL_HINTS):
            return "login"

    # Destructive key chord (⌘⌫ and friends), or destructive/payment wording.
    combo = (decision.get("combo") or "").lower()
    if act == "key" and "cmd" in combo and ("delete" in combo or "backspace" in combo):
        return "confirm"
    if any(h in label for h in _DESTRUCTIVE_HINTS) or any(h in label for h in _PAYMENT_HINTS):
        return "confirm"

    return "auto"


def _has_login_field(observation: dict) -> bool:
    """True while a credential field is still on screen (used to detect when the
    user has finished logging in so the chain can resume)."""
    for e in observation.get("elements", []) or []:
        role = (e.get("role") or "").lower()
        label = " ".join(filter(None, [e.get("title"), e.get("value")])).lower()
        if any(h in role for h in _LOGIN_ROLE_HINTS) or any(h in label for h in _LOGIN_LABEL_HINTS):
            return True
    return False


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if 0 <= a < b:
            try:
                return json.loads(t[a:b + 1])
            except Exception:
                return None
    return None


def _elements_block(elements: list, limit: int = 60) -> str:
    out = []
    for e in elements[:limit]:
        label = (e.get("title") or e.get("value") or "").strip()
        dis = "" if e.get("enabled", True) else " (disabled)"
        # ref FIRST and bracketed so the model can't confuse it with the role.
        out.append(f'ref={e["ref"]}  role={e.get("role","")}  label="{label[:50]}"{dis}')
    return "\n".join(out)


def _history_block(history: list, limit: int = 8) -> str:
    if not history:
        return "(none yet)"
    return "\n".join(
        f'{h["step"]}. {h["action"]} {h.get("target") or h.get("app") or ""} '
        f'-> {"ok" if h["ok"] else "FAILED: " + (h.get("msg") or "")}'
        for h in history[-limit:])


# Default model for the decide step. Driving a UI is agentic reasoning, not a
# quick lookup, so this defaults to Sonnet (overridable) — Haiku is too unreliable
# at picking the right element/action step after step.
_DECIDE_MODEL = "claude-sonnet-4-6"

# Forced tool — guarantees a schema-valid action every step (no JSON-parse flake).
_NEXT_ACTION_TOOL = {
    "name": "next_action",
    "description": "The single next action to take toward the goal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "ref": {"type": "string", "description": "element ref id (e0, e1, …) for click/type"},
            "text": {"type": "string", "description": "text to type (for action=type)"},
            "app": {"type": "string", "description": "app name (for action=open_app)"},
            "combo": {"type": "string", "description": "key chord like cmd+s (for action=key)"},
            "reason": {"type": "string", "description": "one short clause"},
        },
        "required": ["action", "reason"],
    },
}


_DECIDE_SYSTEM = (
    "You drive a macOS app to accomplish a goal, ONE step at a time. You are given "
    "the goal, the focused window's accessibility elements (each line: ref=<id> "
    "role=<role> label=<text>), and the steps already taken. The \"ref\" you put in "
    "your JSON MUST be one of the ref ids shown (e0, e1, e2, …) — never a role or a "
    "label. Choose the SINGLE next action. Reply with STRICT JSON only:\n"
    '{"action":"click","ref":"<ref>","reason":"..."}\n'
    '{"action":"type","ref":"<field ref>","text":"<text>","reason":"..."}\n'
    '{"action":"key","combo":"cmd+s","reason":"..."}\n'
    '{"action":"open_app","app":"<name>","reason":"..."}\n'
    '{"action":"done","reason":"goal achieved"}\n'
    '{"action":"fail","reason":"why it cannot be done"}\n'
    "Rules:\n"
    "- To ENTER TEXT, use action \"type\" with the ref of the text field/area "
    "(role AXTextField / AXTextArea / AXSearchField). Do NOT click it first — type "
    "focuses it for you.\n"
    "- Use \"click\" only for buttons, links, menu items, checkboxes, popups. Never "
    "click a container (AXGroup, AXScrollArea, AXWindow) or an element with no label.\n"
    "- Use \"key\" for keyboard shortcuts (save = cmd+s, select-all = cmd+a).\n"
    "- Use only refs from the list. When the goal is already satisfied, return done. "
    "If a step just failed, do something DIFFERENT — never repeat the same failed step; "
    "if you can't make progress, return fail. Keep reason to one short clause."
)


def _extract_decision(resp) -> dict:
    """Pull the decision from a tool_use block (production) or, failing that, from
    JSON in a text block (the FakeClient path in tests)."""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            return dict(getattr(block, "input", {}) or {})
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return _parse_json(text) or {}
    return {}


async def _decide(client, goal: str, observation: dict, history: list,
                  model: str = _DECIDE_MODEL) -> dict:
    elements = observation.get("elements") or []
    user = (
        f"GOAL: {goal}\n\n"
        f"FOCUSED APP: {observation.get('app','?')}\n"
        f"ELEMENTS:\n{_elements_block(elements)}\n\n"
        f"STEPS SO FAR:\n{_history_block(history)}\n\n"
        "Call next_action with the single next step."
    )
    content = [{"type": "text", "text": user}]
    img = observation.get("image")
    if img:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": img["media_type"], "data": img["b64"]}})
    try:
        resp = await client.messages.create(
            model=model, max_tokens=300, system=_DECIDE_SYSTEM,
            messages=[{"role": "user", "content": content}],
            tools=[_NEXT_ACTION_TOOL],
            tool_choice={"type": "tool", "name": "next_action"},
        )
        data = _extract_decision(resp)
    except Exception as e:
        log.warning("decide failed: %s", e)
        return {"action": "fail", "reason": f"could not decide ({e})"}
    if data.get("action") not in _ACTIONS:
        return {"action": "fail", "reason": "no valid action chosen"}
    return data


async def _execute(actor, ax_executor, decision: dict, app: Optional[str]):
    """Run one decided action through `actor`. Returns an ActionResult.

    `actor` is the executor that performs the act: the gating SafeExecutor for a
    'confirm' step (pops a card) or the raw executor for an 'auto' step (hands-off,
    no card). `ax_executor` is always the raw one, used only for benign focus."""
    act = decision.get("action")
    if act == "open_app":
        return await actor.open_app(decision.get("app") or "")
    if act == "key":
        return await actor.key_combo(decision.get("combo") or "", app=app)
    if act == "type":
        ref = decision.get("ref")
        if ref and ax_executor is not None:
            await ax_executor.focus_element(ref)             # benign focus, no confirm
        # send_keystroke activates the target app first, so the text lands there
        # (synthetic input always goes to the focused app — the per-step confirm
        # keeps the user in the loop if focus is somewhere unexpected).
        return await actor.send_keystroke(app or "", decision.get("text") or "")
    if act == "click":
        return await actor.click_element(ref=decision.get("ref"), app=app)
    # done / fail never reach here
    from action_executor import ActionResult, Capability
    return ActionResult.failure(Capability.CLICK_ELEMENT, error="noop", message="nothing to do")


async def _await_login(executor, app, kill_switch, emit, *,
                       poll_s: float = 2.0, timeout_s: float = 150.0) -> str:
    """Hand credential entry to the human, then wait for the login form to clear.

    Returns 'resume' once the credential field is gone (user logged in), 'halted'
    if the kill switch fires, or 'timeout' if they don't finish in time. VALET
    never touches the secret — it just watches for the field to disappear."""
    import asyncio
    waited = 0.0
    while waited < timeout_s:
        if kill_switch is not None and kill_switch.is_engaged():
            return "halted"
        await asyncio.sleep(poll_s)
        waited += poll_s
        obs = await perception.build_observation(executor, app=app)
        if not _has_login_field(obs):
            return "resume"
    return "timeout"


async def run_loop(
    executor, goal: str, client, *,
    app: Optional[str] = None,
    max_steps: int = _DEFAULT_MAX_STEPS,
    kill_switch=None,
    ax_executor=None,
    emit: Optional[Callable[..., Awaitable[None]]] = None,
    hands_off: bool = False,
) -> dict:
    """Run the supervised observe→decide→act loop for `goal`.

    Returns {status, steps, history, message}. status ∈ {done, failed, vetoed,
    halted, capped, paused}. The kill switch is checked every iteration.

    When `hands_off` is False (default) every mutating step goes through the
    confirm card. When True, each step is risk-classified (`_classify_step`):
    safe steps (clicks/navigation) run straight through for a hands-off chain,
    destructive/payment steps still pop the confirm card, and a credential field
    hands off to the human (VALET waits, then resumes). The kill switch is
    re-checked immediately before every auto (un-carded) act."""
    async def _emit(kind, title, detail="", status="active"):
        if emit:
            try:
                await emit(kind, title, detail=detail, status=status)
            except Exception:
                pass

    if not client:
        return {"status": "failed", "steps": 0, "history": [], "message": "No model available, sir."}

    history: list = []
    consecutive_fails = 0

    for step in range(1, max_steps + 1):
        if kill_switch is not None and kill_switch.is_engaged():
            await _emit("act", "Stopped.", status="error")
            return {"status": "halted", "steps": step - 1, "history": history, "message": "Halted, sir."}

        await _emit("observe", f"Step {step}: looking at the screen")
        observation = await perception.build_observation(executor, app=app)

        decision = await _decide(client, goal, observation, history)
        act = decision.get("action")
        summary = decision.get("reason", "")
        await _emit("decide", f"{act} {decision.get('target') or decision.get('app') or decision.get('combo') or decision.get('ref') or ''}".strip(),
                    detail=summary)

        if act == "done":
            return {"status": "done", "steps": step - 1, "history": history,
                    "message": summary or "Done, sir."}
        if act == "fail":
            return {"status": "failed", "steps": step - 1, "history": history,
                    "message": summary or "I couldn't complete that, sir."}

        # Hybrid autonomy: pick the actor (raw = no card, safe = card) per step,
        # and hand a credential field to the human instead of acting on it.
        actor = executor
        if hands_off:
            risk = _classify_step(decision, observation)
            if risk == "login":
                await _emit("act", "Over to you for the login, sir.",
                            detail="I'll continue once you're signed in.", status="active")
                outcome = await _await_login(executor, app, kill_switch, emit)
                if outcome == "halted":
                    return {"status": "halted", "steps": step - 1, "history": history,
                            "message": "Halted, sir."}
                if outcome == "timeout":
                    return {"status": "paused", "steps": step - 1, "history": history,
                            "message": "I'll leave the login with you, sir — say 'continue' when you're in."}
                await _emit("act", "Signed in — carrying on, sir.", status="done")
                history.append({"step": step, "action": "login", "target": "credentials",
                                "ok": True, "msg": "handed off to user"})
                continue
            if risk == "auto" and ax_executor is not None:
                # Un-carded — re-check the kill switch right before acting.
                if kill_switch is not None and kill_switch.is_engaged():
                    await _emit("act", "Stopped.", status="error")
                    return {"status": "halted", "steps": step - 1, "history": history, "message": "Halted, sir."}
                actor = ax_executor

        result = await _execute(actor, ax_executor, decision, app)
        ok = bool(getattr(result, "ok", False))
        history.append({"step": step, "action": act,
                        "target": decision.get("ref") or decision.get("app") or decision.get("combo"),
                        "ok": ok, "msg": getattr(result, "message", "")})
        await _emit("act", getattr(result, "message", "") or act,
                    status="done" if ok else "error")

        if ok:
            consecutive_fails = 0
            continue
        # A denial is the user vetoing this step — stop the whole task.
        if getattr(result, "error", None) == "denied":
            return {"status": "vetoed", "steps": step, "history": history, "message": "Stopped, sir."}
        if getattr(result, "error", None) == "kill_switch":
            return {"status": "halted", "steps": step, "history": history, "message": "Halted, sir."}
        # Otherwise re-observe and let the model adjust — but don't loop blindly.
        consecutive_fails += 1
        if consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
            return {"status": "failed", "steps": step, "history": history,
                    "message": "I got stuck, sir — stopping rather than guessing."}

    return {"status": "capped", "steps": max_steps, "history": history,
            "message": "That took too many steps, sir — stopping."}
