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

import asyncio
import json
import logging
import re
from typing import Awaitable, Callable, Optional

import perception

log = logging.getLogger("valet.loop")

# Hard ceiling on loop iterations — a runaway backstop, not a target.
_DEFAULT_MAX_STEPS = 8
# Bail after this many consecutive non-veto failures (can't make progress).
_MAX_CONSECUTIVE_FAILS = 2
# Pause after an action before re-observing, so a just-opened menu/popup or a page
# transition has time to RENDER before the next screenshot. Without this the loop
# screenshots mid-animation and "can't see" a menu the user clearly can.
_ACT_SETTLE = 0.5

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
# Strong cues that the page IS a login/sign-in STEP (account chooser, sign-in
# form) — not just a stray "Sign in" header link. On these, VALET hands the WHOLE
# login to the user: picking an account and using a saved password is the
# sensitive part and exactly the flaky-to-click part. Kept specific on purpose so
# ordinary pages with a sign-in link don't false-trigger.
_LOGIN_PAGE_HINTS = (
    "choose an account", "use another account", "forgot email", "forgot password",
    "email or phone", "enter your password", "couldn't sign you in",
    "to continue to gmail", "sign in with your google account",
)


# Browser app names: web content needs a real synthetic mouse click — AXPress
# no-ops on a Gmail row / page button (same reason as the one-shot click path).
_BROWSER_APPS = {
    "google chrome", "chrome", "chromium", "safari", "safari technology preview",
    "firefox", "firefox developer edition", "microsoft edge", "edge", "arc",
    "brave browser", "brave", "opera", "vivaldi", "duckduckgo",
}


def _is_browser(app: Optional[str]) -> bool:
    return bool(app) and app.strip().lower() in _BROWSER_APPS


def _page_elements(observation: dict) -> list:
    """The candidate elements to show the model. For a browser, DROP everything
    that sits entirely above the web-content top (the browser's own toolbar,
    profile/avatar button, tab strip, address bar, extensions) so the loop can
    only target the PAGE — never Chrome's chrome (which is why 'log out of Gmail'
    kept hitting the Chrome profile button). No-op when not a browser or when the
    web-content top is unknown."""
    els = observation.get("elements") or []
    if not _is_browser(observation.get("app")):
        return els
    web_top = observation.get("web_top")
    if not web_top:
        return els
    kept = []
    for e in els:
        fr = e.get("frame")
        if fr and len(fr) == 4 and (fr[1] + fr[3]) <= web_top + 4:
            continue  # entirely in the toolbar band → browser chrome, not the page
        kept.append(e)
    # Safety: if the filter nuked everything (odd geometry), fall back to all.
    return kept or els


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


def _is_login_page(observation: dict) -> bool:
    """True if the focused screen is anywhere in the login flow — account chooser
    OR credential entry. Used to detect when login is fully DONE (resume only once
    none of this remains)."""
    if _has_login_field(observation):
        return True
    for e in observation.get("elements", []) or []:
        label = " ".join(filter(None, [e.get("title"), e.get("value")])).lower()
        if any(h in label for h in _LOGIN_PAGE_HINTS):
            return True
    return False


# Credential-ENTRY cues (the password step) — distinct from the account chooser.
# VALET clicks through the chooser itself and only HANDS OFF here, where secrets
# are entered.
_CREDENTIAL_PAGE_HINTS = (
    "enter your password", "forgot password", "show password", "wrong password",
    "couldn't sign you in", "your password", "passkey",
)


def _is_credential_page(observation: dict) -> bool:
    """True only at the credential-ENTRY step (a secure field or 'enter your
    password'), NOT the account chooser — so VALET picks the account itself and
    hands off just for the password."""
    if _has_login_field(observation):
        return True
    for e in observation.get("elements", []) or []:
        label = " ".join(filter(None, [e.get("title"), e.get("value")])).lower()
        if any(h in label for h in _CREDENTIAL_PAGE_HINTS):
            return True
    return False


# ── Gmail sign-in wall (issue #284) ──────────────────────────────────────────
# The MVP Gmail flow deliberately STARTS signed out: VALET opens gmail.com and,
# rather than typing credentials, detects Google's sign-in wall and hands the
# login to the user with a spoken pause/resume ("tell me when you're in"). Kept
# Gmail/Google-specific on purpose — these phrases appear only on Google's own
# sign-in pages, never on a loaded inbox, so pairing "sign-in phrase present"
# with "no inbox visible" makes the detection robust.
_GMAIL_SIGNIN_HINTS = (
    "to continue to gmail", "sign in with your google account",
    "use your google account", "choose an account", "use another account",
    "forgot email", "enter your password", "couldn't sign you in",
    "email or phone",
)
_GMAIL_INBOX_HINTS = (
    "compose", "search mail", "search in mail", "primary", "snoozed",
    "conversation list", "more emails", "inbox",
)


def _is_gmail_signin(observation: dict) -> bool:
    """True when the focused browser page is Google's sign-in wall for Gmail and
    NO inbox is present — i.e. the user is signed out. The loop uses this to pause
    and ask the human to log in; VALET never types the credentials itself."""
    if not _is_browser(observation.get("app")):
        return False
    blob = " ".join(
        " ".join(filter(None, [e.get("title"), e.get("value")]))
        for e in (observation.get("elements") or [])
    ).lower()
    if not blob:
        return False
    signed_out = any(h in blob for h in _GMAIL_SIGNIN_HINTS)
    has_inbox = any(h in blob for h in _GMAIL_INBOX_HINTS)
    return signed_out and not has_inbox


def _labels_blob(observation: dict) -> str:
    return " ".join(
        " ".join(filter(None, [e.get("title"), e.get("value")]))
        for e in (observation.get("elements") or [])
    ).lower()


_EMAIL_RE = re.compile(r'[\w.+-]+@[\w.-]+\.\w+')


def _is_account_chooser(observation: dict) -> bool:
    """True on Google's 'Choose an account' screen."""
    return "choose an account" in _labels_blob(observation)


def _account_emails(observation: dict) -> list:
    """Distinct real account emails shown on the chooser, in screen order. Skips
    the 'use another account' / 'remove an account' control rows."""
    seen: list = []
    for e in observation.get("elements", []) or []:
        label = " ".join(filter(None, [e.get("title"), e.get("value")]))
        low = label.lower()
        if "use another account" in low or "remove an account" in low:
            continue
        for m in _EMAIL_RE.findall(label):
            if m not in seen:
                seen.append(m)
    return seen


def _is_password_page(observation: dict) -> bool:
    """True on the Google password-entry step (a secure field, or the 'Enter your
    password' heading)."""
    if _has_login_field(observation):
        return True
    return "enter your password" in _labels_blob(observation)


def _password_account_email(observation: dict) -> str:
    """The account email shown on the password page (the chip under 'Hi <name>')."""
    emails = _account_emails(observation)
    return emails[0] if emails else ""


def _find_account_element(observation: dict, email: str) -> dict:
    """The chooser row element whose label contains `email` — a deterministic
    match off the AX tree (the rows carry the exact address), so we click the
    RIGHT account instead of letting vision guess between similar names."""
    email_l = (email or "").lower()
    if not email_l:
        return {}
    for e in observation.get("elements", []) or []:
        label = " ".join(filter(None, [e.get("title"), e.get("value")])).lower()
        if "use another account" in label or "remove an account" in label:
            continue
        if email_l in label:
            return e
    return {}


# Passkey / 2-step / biometric screens — including the separate Bitwarden passkey
# popup window. ONLY the human can complete these (Touch ID is hardware), so VALET
# must pause cleanly here rather than trying to drive them (which loops).
_GMAIL_HUMAN_STEP_HINTS = (
    "verifying it",                       # "Verifying it's you" (any apostrophe)
    "complete sign-in using your passkey", "use your passkey", "with your passkey",
    "log in with passkey", "no passkeys found", "use your device or hardware key",
    "2-step verification", "verification code", "get a verification code",
    "check your phone", "google prompt", "use your fingerprint", "touch id",
    "confirm your recovery",
)


def _is_gmail_human_step(observation: dict) -> bool:
    """True on a passkey / 2-step / biometric screen only the human can finish."""
    blob = _labels_blob(observation)
    return any(h in blob for h in _GMAIL_HUMAN_STEP_HINTS)


def _is_gmail_inbox(observation: dict) -> bool:
    """True once the Gmail inbox is loaded (sign-in genuinely complete)."""
    blob = _labels_blob(observation)
    return any(h in blob for h in _GMAIL_INBOX_HINTS) and not _is_gmail_signin(observation)


def _account_prompt(emails: list) -> str:
    if len(emails) >= 2:
        return f"Which account, sir — {emails[0]} or {emails[1]}?"
    if len(emails) == 1:
        return f"Shall I sign you into {emails[0]}, sir?"
    return "Which account should I use, sir?"


def _approve_prompt(account: str) -> str:
    who = f" into {account}" if account else ""
    return f"Shall I sign you in{who} with your saved password, sir?"


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
            "target": {"type": "string", "description": "for action=click ONLY: the short visible label of a control you can SEE on the screenshot but that is NOT in the elements list (e.g. an item inside a popup that just opened, like 'Sign out of all accounts'). Vee locates it visually. Leave ref empty when you use this."},
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
    "- LOGIN: if a password field is ALREADY FILLED (it shows masked dots, e.g. the "
    "browser autofilled a saved login), do NOT type — click the Sign in / Log in / "
    "Submit / Next button to log in. Only when the password field is EMPTY should you "
    "target it with \"type\" (which hands credential entry to the user). Never type a "
    "made-up password.\n"
    "- You are ALREADY looking at FOCUSED APP — the elements shown ARE its current "
    "screen. Do NOT use open_app for the app you're already in (it's a no-op and wastes "
    "a step); act on the screen instead. Only use open_app to switch to a DIFFERENT app "
    "that isn't focused.\n"
    "- BROWSER vs PAGE: in a browser (Chrome, Safari, Arc, Edge…) the goal is almost "
    "always about the WEB PAGE, not the browser itself. Act on elements INSIDE the page "
    "content. AVOID the browser's own chrome — the toolbar, address bar, tab strip, "
    "extension icons, and ESPECIALLY the browser's profile/avatar button (it opens the "
    "browser's profile switcher — 'Other Chrome Profiles', 'Turn on sync', 'Manage "
    "Profiles' — NOT the website's account menu). To sign out of a web app like Gmail, "
    "click the account avatar INSIDE the page (the one whose label names the Google "
    "Account / email, top-right of the page content), then the site's own 'Sign out' "
    "item — never the browser's profile menu.\n"
    "- DO THE WORK — do not declare success without acting. NEVER return 'done' on "
    "the first step (no steps taken yet): take a real first action. Only return 'done' "
    "when the goal's END STATE is visibly true on screen RIGHT NOW. For 'log out / sign "
    "out', done means you SEE a signed-out / account-chooser / login screen — an open "
    "inbox or dashboard means you are still signed IN, so proceed (click the in-page "
    "account avatar, then 'Sign out'). Do not assume or hallucinate completion.\n"
    "- BEWARE the wrong sign-in page: a page that says 'continue to Gmail' or 'this "
    "account will be available to other apps' is an ADD-ANOTHER-ACCOUNT page (it "
    "appears if you clicked 'Add another account' by mistake) — that is NOT a sign-out. "
    "In the account menu, 'Sign out of all accounts' is the LAST/bottom item, directly "
    "BELOW 'Add another account' — target the bottom one. If you landed on an add-"
    "account page, go back and click the correct row.\n"
    "- POPUPS / MENUS: after you click something that opens a menu or popup (an "
    "account avatar, a ⋮ button, a dropdown), the next step is to click an ITEM "
    "INSIDE it — do NOT click the opener again (that just closes it). If that item "
    "is visible on the screenshot but missing from the elements list, use action "
    "click with 'target' set to its visible label (e.g. 'Sign out of all "
    "accounts') and no ref — Vee will find it visually.\n"
    "- Prefer a ref from the list when one matches; use 'target' only for visible "
    "items the list is missing. If a step just failed, do something DIFFERENT — never "
    "repeat the same failed step; if you genuinely cannot make progress after trying, "
    "return fail (not done). Keep reason to one short clause."
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
    # In a browser, only the PAGE is offered as candidates — the toolbar/profile
    # buttons are filtered out so the model can't pick Chrome's own chrome.
    elements = _page_elements(observation)
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


async def _execute(actor, ax_executor, decision: dict, app: Optional[str],
                   observation: Optional[dict] = None):
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
        # In a browser, AXPress on web content (a Gmail avatar, a page button)
        # often does nothing — click the element's location with a real mouse
        # instead. Native apps keep AXPress-by-ref (works regardless of focus).
        obs_app = (observation or {}).get("app") or app
        pt = decision.get("_point")          # vision-resolved point (no ref)
        if pt and len(pt) == 2:
            return await actor.click_element(point=(float(pt[0]), float(pt[1])), app=obs_app)
        el = _find_element(observation or {}, decision.get("ref"))
        fr = el.get("frame")
        if _is_browser(obs_app) and fr and len(fr) == 4:
            cx, cy = fr[0] + fr[2] / 2.0, fr[1] + fr[3] / 2.0
            return await actor.click_element(point=(cx, cy), app=obs_app)
        # Use the OBSERVED app (obs_app), like the branches above — not the
        # run_loop `app` param, which can differ from what's actually focused and
        # would send the click to the wrong app.
        return await actor.click_element(ref=decision.get("ref"), app=obs_app)
    # done / fail never reach here
    from action_executor import ActionResult, Capability
    return ActionResult.failure(Capability.CLICK_ELEMENT, error="noop", message="nothing to do")


async def _await_login(executor, app, kill_switch, emit, *,
                       poll_s: float = 2.0, timeout_s: float = 150.0) -> str:
    """Hand credential entry to the human, then wait for the login form to clear.

    Returns 'resume' once the credential field is gone (user logged in), 'halted'
    if the kill switch fires, or 'timeout' if they don't finish in time. VALET
    never touches the secret — it just watches for the login screen to clear."""
    waited = 0.0
    while waited < timeout_s:
        if kill_switch is not None and kill_switch.is_engaged():
            return "halted"
        await asyncio.sleep(poll_s)
        waited += poll_s
        obs = await perception.build_observation(executor, app=app)
        if not _is_login_page(obs):
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
    speak: Optional[Callable[[str], Awaitable[None]]] = None,
    login_choice: Optional[dict] = None,
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
    forced_recheck = False
    login_handoffs = 0

    async def _gmail_vision_click(obs: dict, desc: str) -> bool:
        """Locate `desc` on the current screen (vision resolver) and click it with
        a real mouse — the path that works on web content the AX tree misses (an
        account row, the saved-password chip). Best-effort; returns True on click."""
        obs_app = obs.get("app") or app
        try:
            import target_resolver
            res = await target_resolver.resolve(obs, desc, client, intent="click")
        except Exception as e:
            log.warning("gmail resolve '%s' failed: %s", desc, e)
            return False
        actor2 = ax_executor or executor
        st = getattr(res, "status", None) if res is not None else None
        if st == "ref" and getattr(res, "ref", None):
            r = await actor2.click_element(ref=res.ref, app=obs_app)
            return bool(getattr(r, "ok", False))
        if st == "point" and getattr(res, "point", None):
            p = res.point
            r = await actor2.click_element(point=(float(p[0]), float(p[1])), app=obs_app)
            return bool(getattr(r, "ok", False))
        return False

    async def _gmail_click_next(obs: dict) -> bool:
        """Click the Next / Sign in button from the AX tree (browser: by point).
        Returns True if a matching button was clicked."""
        obs_app = obs.get("app") or app
        actor2 = ax_executor or executor
        for e in obs.get("elements", []) or []:
            role = (e.get("role") or "").lower()
            label = " ".join(filter(None, [e.get("title"), e.get("value")])).lower().strip()
            if ("button" in role or "link" in role) and label in (
                    "next", "sign in", "continue", "log in", "login"):
                fr = e.get("frame")
                if _is_browser(obs_app) and fr and len(fr) == 4:
                    r = await actor2.click_element(
                        point=(fr[0] + fr[2] / 2.0, fr[1] + fr[3] / 2.0), app=obs_app)
                else:
                    r = await actor2.click_element(ref=e.get("ref"), app=obs_app)
                if getattr(r, "ok", False):
                    return True
        return False

    async def _login_handoff(step):
        """Speak + show the credential hand-off, wait for the user to sign in,
        then resume. Returns a terminal result dict to RETURN, or None to CONTINUE
        the loop. Spoken AND panelled so the prompt isn't silent."""
        prompt = ("Sign in with your saved login, sir — I'll carry on once you're in.")
        await _emit("act", "The sign-in's yours, sir.",
                    detail="Pick your account / enter your password — let the browser "
                           "fill your saved login. I'll carry on once you're in.",
                    status="active")
        if speak:
            try:
                await speak(prompt)
            except Exception:
                pass
        outcome = await _await_login(executor, app, kill_switch, emit)
        if outcome == "halted":
            return {"status": "halted", "steps": step - 1, "history": history,
                    "message": "Halted, sir."}
        if outcome == "timeout":
            return {"status": "paused", "steps": step - 1, "history": history,
                    "message": "I'll leave the login with you, sir — pick your account "
                               "and use your saved password, then say 'continue' when "
                               "you're in."}
        await _emit("act", "Signed in — carrying on, sir.", status="done")
        if speak:
            try:
                await speak("Signed in — carrying on, sir.")
            except Exception:
                pass
        history.append({"step": step, "action": "login", "target": "credentials",
                        "ok": True, "msg": "handed off to user"})
        return None

    for step in range(1, max_steps + 1):
        if kill_switch is not None and kill_switch.is_engaged():
            await _emit("act", "Stopped.", status="error")
            return {"status": "halted", "steps": step - 1, "history": history, "message": "Halted, sir."}

        await _emit("observe", f"Step {step}: looking at the screen")
        observation = await perception.build_observation(executor, app=app)

        # ── Gmail GUIDED login (issue #284) ──────────────────────────────────
        # The user is signed out. VALET never types the password, but it DOES
        # guide the login: it asks which account and clicks it, then at the
        # password step asks approval and clicks the saved-password chip + Next.
        # Each ask is a cross-turn pause (the caller parks it on pending_offer and
        # resumes with the user's spoken answer as `login_choice`).
        if hands_off and (_is_gmail_human_step(observation) or _is_gmail_signin(observation)):
            _paused_app = observation.get("app") or app

            # (0) Passkey / 2-step / biometric — only the human can finish it.
            # Pause ONCE and hand off (never loop trying to drive Touch ID).
            if _is_gmail_human_step(observation):
                await _emit("act", "This one needs your fingerprint, sir.",
                            detail="Clear the passkey box and use Touch ID, then say "
                                   "'I'm logged in'.", status="active")
                return {"status": "paused", "reason": "login", "resume_goal": goal,
                        "app": _paused_app, "steps": step - 1, "history": history,
                        "message": "This one needs you, sir — clear the passkey box and "
                                   "use your fingerprint, then tell me when you're in."}

            # (a) Account chooser — ask which account, then click it.
            if _is_account_chooser(observation):
                emails = _account_emails(observation)
                if login_choice and login_choice.get("email"):
                    tgt = login_choice["email"]
                    login_choice = None
                    await _emit("act", f"Selecting {tgt}", status="active")
                    # Deterministic: click the AX row that carries this exact
                    # address; fall back to vision only if it isn't in the tree.
                    clicked = False
                    el = _find_account_element(observation, tgt)
                    if el:
                        obs_app = observation.get("app") or app
                        actor2 = ax_executor or executor
                        fr = el.get("frame")
                        if _is_browser(obs_app) and fr and len(fr) == 4:
                            r = await actor2.click_element(
                                point=(fr[0] + fr[2] / 2.0, fr[1] + fr[3] / 2.0), app=obs_app)
                        else:
                            r = await actor2.click_element(ref=el.get("ref"), app=obs_app)
                        clicked = bool(getattr(r, "ok", False))
                    if not clicked:
                        clicked = await _gmail_vision_click(observation, f"the account row for {tgt}")
                    if clicked:
                        history.append({"step": step, "action": "click", "target": tgt,
                                        "ok": True, "msg": "chose account"})
                        # Wait for the chooser to give way to the next screen.
                        for _ in range(8):
                            await asyncio.sleep(0.7)
                            if kill_switch is not None and kill_switch.is_engaged():
                                return {"status": "halted", "steps": step, "history": history,
                                        "message": "Halted, sir."}
                            if not _is_account_chooser(await perception.build_observation(executor, app=app)):
                                break
                        continue
                    await _emit("act", f"I couldn't select {tgt}", status="error")
                    # fall through to re-ask
                await _emit("act", "Which account, sir?", detail=", ".join(emails), status="active")
                return {"status": "paused", "reason": "choose_account", "accounts": emails,
                        "resume_goal": goal, "app": _paused_app, "steps": step - 1,
                        "history": history, "message": _account_prompt(emails)}

            # (b) Password step — ask approval, then click the chip + Next.
            if _is_password_page(observation):
                account = _password_account_email(observation)
                if login_choice and login_choice.get("approve"):
                    login_choice = None
                    await _emit("act", "Signing you in, sir.", detail=account, status="active")
                    # Click the saved-password suggestion (fills the field); best-
                    # effort — if the field is already autofilled there's no chip.
                    await _gmail_vision_click(observation, "the saved password suggestion popup")
                    await asyncio.sleep(_ACT_SETTLE)
                    obs2 = await perception.build_observation(executor, app=app)
                    if not await _gmail_click_next(obs2):
                        await _gmail_vision_click(obs2, "the Next button to sign in")
                    history.append({"step": step, "action": "signin", "ok": True,
                                    "msg": "submitted saved login"})
                    # Wait for the outcome, distinguishing the inbox (real success)
                    # from a passkey/2-step page (needs the human) — so we never
                    # falsely claim "you're in" while a biometric step is pending.
                    for _ in range(16):
                        await asyncio.sleep(1.3)
                        if kill_switch is not None and kill_switch.is_engaged():
                            return {"status": "halted", "steps": step, "history": history,
                                    "message": "Halted, sir."}
                        o = await perception.build_observation(executor, app=app)
                        if _is_gmail_human_step(o):
                            return {"status": "paused", "reason": "login", "resume_goal": goal,
                                    "app": o.get("app") or _paused_app, "steps": step,
                                    "history": history,
                                    "message": "Nearly there, sir — clear the passkey box and "
                                               "use your fingerprint, then tell me when you're in."}
                        if _is_gmail_inbox(o):
                            return {"status": "done", "reason": "signed_in", "steps": step,
                                    "history": history, "message": "You're in, sir — signed into Gmail."}
                    return {"status": "paused", "reason": "login", "resume_goal": goal,
                            "app": _paused_app, "steps": step, "history": history,
                            "message": "That didn't go through, sir — finish the sign-in "
                                       "and tell me when you're ready."}
                await _emit("act", "Approve sign-in?", detail=account, status="active")
                return {"status": "paused", "reason": "approve_signin", "account": account,
                        "resume_goal": goal, "app": _paused_app, "steps": step - 1,
                        "history": history, "message": _approve_prompt(account)}

            # (c) Some other sign-in screen. If the user already answered, re-
            # observe; otherwise fall back to the plain "log in yourself" hand-off.
            if login_choice:
                login_choice = None
                continue
            return {"status": "paused", "reason": "login", "resume_goal": goal,
                    "app": _paused_app, "steps": step - 1, "history": history,
                    "message": "You're signed out of Gmail, sir. Log in, then tell me "
                               "when you're ready and I'll carry on."}

        # FAST hand-off at the CREDENTIAL step (password) only — VALET still clicks
        # through the account chooser itself (the user wants to see it pick their
        # account), then hands off the instant the password page appears, before a
        # ~5s model decide. (Handing off the whole login skipped the account click.)
        if hands_off and _is_credential_page(observation):
            # Guard against re-handing-off forever: a multi-window login (e.g. a
            # separate password-manager popup) can make _await_login report the
            # form "cleared" and then re-appear. After two hand-offs, stop and
            # leave the sign-in with the user rather than looping.
            login_handoffs += 1
            if login_handoffs > 2:
                return {"status": "paused", "reason": "login", "resume_goal": goal,
                        "app": observation.get("app") or app, "steps": step - 1,
                        "history": history,
                        "message": "I'll leave the sign-in with you, sir — tell me "
                                   "when you're in and I'll carry on."}
            _r = await _login_handoff(step)
            if _r is not None:
                return _r
            continue

        decision = await _decide(client, goal, observation, history)
        act = decision.get("action")
        summary = decision.get("reason", "")
        await _emit("decide", f"{act} {decision.get('target') or decision.get('app') or decision.get('combo') or decision.get('ref') or ''}".strip(),
                    detail=summary)

        # Guard against a lazy first-step "done" (the model glancing at a busy
        # page and declaring victory without acting). Re-observe and re-decide
        # once, with a nudge, before accepting completion. Bounded to one retry.
        if act == "done" and not history and not forced_recheck:
            forced_recheck = True
            await _emit("decide", "Double-checking before I call it done…")
            history.append({"step": step, "action": "recheck", "ok": True,
                            "msg": "claimed done with no action taken — re-evaluating"})
            continue

        if act == "done":
            return {"status": "done", "steps": step - 1, "history": history,
                    "message": summary or "Done, sir."}
        if act == "fail":
            return {"status": "failed", "steps": step - 1, "history": history,
                    "message": summary or "I couldn't complete that, sir."}

        # Redundant open_app: opening the app you're already in is a no-op that
        # "succeeds", so the model can spin on it forever. Treat as a soft failure
        # and nudge it to act on the screen instead.
        if act == "open_app":
            _tgt = (decision.get("app") or "").strip().lower()
            _cur = (observation.get("app") or "").strip().lower()
            if _tgt and _cur and (_tgt == _cur or _tgt in _cur or _cur in _tgt):
                await _emit("decide", f"Already in {observation.get('app')} — acting on the screen instead")
                history.append({"step": step, "action": "open_app", "ok": False,
                                "target": decision.get("app"),
                                "msg": "already in this app — pick a click/type on the visible screen"})
                consecutive_fails += 1
                if consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
                    return {"status": "failed", "steps": step, "history": history,
                            "message": "I got stuck, sir — stopping rather than guessing."}
                continue

        # Vision fallback: the model named a control it can SEE (e.g. a popup item)
        # but couldn't ref. Locate it visually (the same resolver one-shot clicks
        # use) and carry the resolved point on the decision for the click below.
        if act == "click" and decision.get("target") and not _find_element(observation, decision.get("ref")):
            res = None
            try:
                import target_resolver
                res = await target_resolver.resolve(observation, decision["target"], client, intent="click")
            except Exception as e:
                log.warning("loop vision resolve failed: %s", e)
            if res is not None and getattr(res, "status", None) == "ref":
                decision["ref"] = res.ref
            elif res is not None and getattr(res, "status", None) == "point" and res.point:
                decision["_point"] = list(res.point)
            else:
                await _emit("act", f"I can't find '{decision['target']}' on screen", status="error")
                history.append({"step": step, "action": "click", "ok": False,
                                "target": decision.get("target"), "msg": "vision miss"})
                consecutive_fails += 1
                if consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
                    return {"status": "failed", "steps": step, "history": history,
                            "message": "I got stuck, sir — stopping rather than guessing."}
                continue

        # Hybrid autonomy: pick the actor (raw = no card, safe = card) per step,
        # Click through the account chooser ourselves, but hand off at the
        # CREDENTIAL step — VALET picks the account, the user enters the password.
        actor = executor
        if hands_off:
            risk = _classify_step(decision, observation)
            if risk == "login":          # the model targeted a credential field
                _r = await _login_handoff(step)
                if _r is not None:
                    return _r
                continue
            if risk == "auto" and ax_executor is not None:
                # Un-carded — re-check the kill switch right before acting.
                if kill_switch is not None and kill_switch.is_engaged():
                    await _emit("act", "Stopped.", status="error")
                    return {"status": "halted", "steps": step - 1, "history": history, "message": "Halted, sir."}
                actor = ax_executor

        # Visibly glide the cursor onto a click target first, so the user can
        # WATCH the loop work (same affordance as a one-shot click). Browser-safe
        # (verify=False — web hit-tests are unreliable); best-effort, never fatal.
        if act == "click" and ax_executor is not None:
            _pt = decision.get("_point")
            if _pt and len(_pt) == 2:
                _gx, _gy = _pt
            else:
                _el = _find_element(observation, decision.get("ref"))
                _fr = _el.get("frame")
                _gx, _gy = (_fr[0] + _fr[2] / 2.0, _fr[1] + _fr[3] / 2.0) \
                    if (_fr and len(_fr) == 4) else (None, None)
            if _gx is not None:
                try:
                    await ax_executor.glide_to_target(
                        _gx, _gy, ref=decision.get("ref"), verify=False)
                except Exception:
                    pass

        result = await _execute(actor, ax_executor, decision, app, observation)
        ok = bool(getattr(result, "ok", False))
        history.append({"step": step, "action": act,
                        "target": decision.get("ref") or decision.get("app") or decision.get("combo"),
                        "ok": ok, "msg": getattr(result, "message", "")})
        await _emit("act", getattr(result, "message", "") or act,
                    status="done" if ok else "error")

        # Let the UI settle (menu open animation, page nav) before re-observing —
        # otherwise the next screenshot catches it mid-render and the model can't
        # see the menu item it just revealed.
        await asyncio.sleep(_ACT_SETTLE)

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
