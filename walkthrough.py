"""Guided walkthroughs (Stage 3) — teach, don't do.

Given a goal ("show me how to turn on Bluetooth"), produce an ordered step plan
and run a TEACH loop: observe the screen → point the visible cursor at the next
control (NEVER click) → narrate the step → wait + re-observe until the user does
it → advance. The opposite of the UC4 agent loop (which acts): here VALET waits
for the human.

Design for testability: ``run_walkthrough`` takes all I/O as injected callables
(observe / resolve / glide / speak / emit / cancel signals), so the loop logic is
unit-tested with fakes — no real screen, cursor, or LLM. ``step_done`` and
``match_curated`` are pure. Only ``plan_steps`` touches the model, and the server
wires the real perception/resolver/cursor seams in.
"""

from __future__ import annotations

import asyncio
import difflib
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

# Model for plan generation — agentic step decomposition (matches agent_loop/UC4).
_PLANNER_MODEL = "claude-sonnet-4-6"


@dataclass
class Step:
    title: str                 # short label for the panel
    narration: str             # what Vee says for this step
    target: str = ""           # NL description of the control to point at (optional)
    verify: str = ""           # text/label that appears once the step is done (optional)


# ── Curated seeds — hand-tuned for demo reliability. The LLM handles the rest. ──
_CURATED: dict[str, list[Step]] = {
    "bluetooth": [
        Step("Open Bluetooth settings",
             "I'll point you to it, sir. Open System Settings, then choose Bluetooth.",
             target="Bluetooth in the System Settings sidebar", verify="bluetooth"),
        Step("Toggle Bluetooth on",
             "Now flip the Bluetooth switch on at the top.",
             target="the Bluetooth on/off switch", verify="on"),
        Step("Pair a device",
             "Put your device in pairing mode, then click Connect next to it when it appears.",
             target="the Connect button next to your device", verify="connected"),
    ],
    "wifi": [
        Step("Open Wi-Fi settings",
             "Open System Settings, sir, then choose Wi-Fi.",
             target="Wi-Fi in the System Settings sidebar", verify="wi-fi"),
        Step("Turn Wi-Fi on",
             "Flip the Wi-Fi switch on.",
             target="the Wi-Fi on/off switch", verify="on"),
        Step("Join a network",
             "Pick your network from the list and enter the password if it asks.",
             target="your network in the list", verify="connected"),
    ],
    "filevault": [
        Step("Open Privacy & Security",
             "Open System Settings, sir, and choose Privacy and Security.",
             target="Privacy and Security in the sidebar", verify="filevault"),
        Step("Find FileVault",
             "Scroll down to FileVault.",
             target="the FileVault row", verify="turn on"),
        Step("Turn FileVault on",
             "Click Turn On, and keep your recovery key somewhere safe.",
             target="the Turn On button for FileVault", verify="on"),
    ],
}

_CURATED_ALIASES: dict[str, str] = {
    "bluetooth": "bluetooth", "blue tooth": "bluetooth",
    "wifi": "wifi", "wi-fi": "wifi", "wi fi": "wifi", "wireless": "wifi",
    "filevault": "filevault", "file vault": "filevault", "disk encryption": "filevault",
    "encryption": "filevault",
}


def match_curated(goal: str) -> Optional[list[Step]]:
    """Return curated steps if the goal clearly maps to a seeded walkthrough."""
    g = (goal or "").lower()
    for phrase, key in _CURATED_ALIASES.items():
        if phrase in g:
            return list(_CURATED[key])
    # fuzzy single-token fallback for STT slips
    close = difflib.get_close_matches(g, list(_CURATED_ALIASES.keys()), n=1, cutoff=0.88)
    if close:
        return list(_CURATED[_CURATED_ALIASES[close[0]]])
    return None


# ── Model-generated plans (forced-tool JSON, mirrors agent_loop._decide) ────────
_PLANNER_TOOL = {
    "name": "walkthrough_steps",
    "description": "An ordered list of short steps that TEACH the user how to do "
                   "something on macOS. Each step is narrated aloud and points at a "
                   "control; VALET never clicks for the user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "short panel label"},
                        "narration": {"type": "string",
                                      "description": "one butler-brief sentence to say"},
                        "target": {"type": "string",
                                   "description": "natural-language control to point at, "
                                                  "or empty if nothing to point at"},
                        "verify": {"type": "string",
                                   "description": "a word/label visible on screen once this "
                                                  "step is done, or empty"},
                    },
                    "required": ["title", "narration"],
                },
            },
        },
        "required": ["steps"],
    },
}

_PLANNER_SYSTEM = (
    "You are VALET, a British butler, teaching the user how to do something on "
    "their Mac. Produce a short ordered list of steps. Each narration is ONE brief "
    "sentence, calm and direct, no em-dashes. For each step, name the on-screen "
    "control to point at (target) and a word that will be visible once the step is "
    "done (verify). You only TEACH and POINT; never instruct as if you clicked it."
)


async def plan_steps(goal: str, client, observation: Optional[dict] = None) -> list[Step]:
    """Ask the model for an ordered step plan (forced tool-use → schema-valid)."""
    app = (observation or {}).get("app", "?")
    user = f"Goal: {goal}\nCurrent app: {app}\nGenerate the walkthrough steps."
    resp = await client.messages.create(
        model=_PLANNER_MODEL, max_tokens=900, system=_PLANNER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        tools=[_PLANNER_TOOL],
        tool_choice={"type": "tool", "name": "walkthrough_steps"},
    )
    block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
    raw = (block.input or {}).get("steps", []) if block else []
    steps: list[Step] = []
    for s in raw:
        title = (s.get("title") or "").strip()
        narration = (s.get("narration") or "").strip()
        if not narration:
            continue
        steps.append(Step(title or narration[:40], narration,
                          (s.get("target") or "").strip(), (s.get("verify") or "").strip()))
    return steps


# ── Completion detection (pure, unit-tested) ───────────────────────────────────
def _texts(obs: dict) -> set[str]:
    out: set[str] = set()
    for e in obs.get("elements", []) or []:
        for k in ("title", "value"):
            v = str(e.get(k) or "").strip().lower()
            if v:
                out.add(v)
    a = str(obs.get("app") or "").strip().lower()
    if a:
        out.add(a)
    return out


def step_done(before: dict, after: dict, step: Step) -> bool:
    """Heuristic: did the user complete this step? Honest, not magic — drives
    auto-advance. Uses the step's `verify` hint when present, else a meaningful
    on-screen change."""
    bt, at = _texts(before), _texts(after)
    v = (step.verify or "").strip().lower()
    if v:
        in_after = any(v in t for t in at)
        in_before = any(v in t for t in bt)
        if in_after and not in_before:
            return True
        # app switched AND the verify word is now visible (e.g. opened the pane)
        return in_after and (str(after.get("app") or "").lower()
                             != str(before.get("app") or "").lower())
    # no explicit verify → require a meaningful change
    if str(after.get("app") or "") != str(before.get("app") or ""):
        return True
    return len(at - bt) >= 2


def _point_of(res) -> Optional[tuple[float, float]]:
    """Screen point to glide to from a resolver Resolution (frame center, else point)."""
    frame = getattr(res, "frame", None)
    if frame and len(frame) == 4:
        return (frame[0] + frame[2] / 2.0, frame[1] + frame[3] / 2.0)
    pt = getattr(res, "point", None)
    if pt and len(pt) == 2:
        return (pt[0], pt[1])
    return None


# ── The teach loop ─────────────────────────────────────────────────────────────
@dataclass
class _LoopDeps:
    observe: Callable[[], Awaitable[dict]]
    resolve: Callable[[dict, str], Awaitable[object]]
    glide: Callable[..., Awaitable[None]]
    speak: Callable[[str], Awaitable[None]]
    emit: Callable[..., Awaitable[None]]
    should_cancel: Callable[[], bool] = lambda: False
    kill_engaged: Callable[[], bool] = lambda: False
    # Returns None | "next" | "doit" | "stop" — a voice signal during the wait.
    wait_signal: Callable[[], Optional[str]] = lambda: None
    do_it: Optional[Callable[[str], Awaitable[None]]] = None  # gated click for "do it for me"


async def run_walkthrough(
    *,
    goal: str,
    steps: list[Step],
    deps: _LoopDeps,
    poll_interval: float = 1.5,
    step_timeout: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    """Observe → point → narrate → wait+re-observe → advance, for each step.
    Returns {status: done|halted|empty, message}. Never clicks (except an explicit
    'do it for me' routed through deps.do_it, which is the gated path)."""
    if not steps:
        return {"status": "empty",
                "message": "I'm not sure how to walk you through that, sir."}
    total = len(steps)
    for i, step in enumerate(steps):
        if deps.kill_engaged() or deps.should_cancel():
            return {"status": "halted", "message": "Stopped, sir."}
        await deps.emit(f"Step {i + 1}/{total}: {step.title}",
                        detail=step.narration, status="active")
        before = await deps.observe()

        if step.target:
            res = await deps.resolve(before, step.target)
            pt = _point_of(res)
            if pt is not None:
                await deps.glide(pt[0], pt[1], getattr(res, "ref", None),
                                 getattr(res, "label", "") or step.target)
            else:
                # Honest miss — never a wild point.
                await deps.speak(f"I can't see {step.target} on screen, sir.")
        await deps.speak(step.narration)

        outcome = await _await_step(step, before, deps, poll_interval, step_timeout,
                                    clock, sleep)
        if outcome == "halt":
            return {"status": "halted", "message": "Stopped, sir."}
        if outcome == "stall":
            await deps.speak("We can pick this up when you're ready, sir.")
            return {"status": "stalled", "message": "Paused the walkthrough, sir."}
        if outcome == "doit" and deps.do_it is not None and step.target:
            await deps.do_it(step.target)  # routes through the Tier-1 gate
        await deps.emit(f"Step {i + 1} done", status="done")

    await deps.speak("That's the last step, sir.")
    return {"status": "done", "message": "All set, sir."}


async def _await_step(step, before, deps, interval, timeout, clock, sleep) -> str:
    """Wait until the step is done, a voice signal arrives, or we give up.
    Returns 'advance' | 'halt' | 'doit' | 'stall'. Re-narrates once at `timeout`;
    a hard cap (4× timeout) stops the loop ever running away if the user wanders off."""
    start = clock()
    renarrated = False
    while True:
        if deps.kill_engaged() or deps.should_cancel():
            return "halt"
        sig = deps.wait_signal()
        if sig == "stop":
            return "halt"
        if sig == "next":
            return "advance"
        if sig == "doit":
            return "doit"
        cur = await deps.observe()
        if step_done(before, cur, step):
            return "advance"
        elapsed = clock() - start
        if not renarrated and elapsed > timeout:
            renarrated = True
            await deps.speak("Take your time, sir. Say next when you're ready, "
                             "or do it for me.")
        if elapsed > timeout * 4:  # hard cap — never spin forever
            return "stall"
        await sleep(interval)
