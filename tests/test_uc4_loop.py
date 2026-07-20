"""UC4 — observe→decide→act loop: headless unit tests.

No GUI/API key. A FakeClient feeds a scripted sequence of decisions and a
FakeExec records actions, so the controller's contract is exercised offline:
done-detection, the hard step cap, veto (denied) stop, kill-switch halt,
stuck-recovery bail, and that every beat is emitted. Capture is monkeypatched
off so build_observation uses the AX list only.

Run:  ./.venv/bin/python -m pytest tests/test_uc4_loop.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_loop
import perception
from action_executor import ActionResult, Capability

ELS = [
    {"ref": "e0", "role": "AXWindow", "title": "App", "value": "", "enabled": True, "frame": [0, 0, 800, 600]},
    {"ref": "e1", "role": "AXButton", "title": "Save", "value": "", "enabled": True, "frame": [10, 20, 80, 24]},
    {"ref": "e2", "role": "AXTextField", "title": "Name", "value": "", "enabled": True, "frame": [10, 60, 200, 24]},
]


class _Resp:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class FakeClient:
    """Serves a scripted list of decision JSON strings, last one repeats."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.messages = self
        self.n = 0

    async def create(self, **kw):
        i = min(self.n, len(self.scripted) - 1)
        self.n += 1
        return _Resp(self.scripted[i])


class FakeExec:
    def __init__(self, click_ok=True, click_error=None):
        self.click_ok = click_ok
        self.click_error = click_error
        self.actions = []

    async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
        return ActionResult.success(Capability.OBSERVE_UI, data={"app": "App", "elements": ELS})

    async def click_element(self, *, ref=None, point=None, app=None, task_id=None):
        self.actions.append(("click", ref))
        if self.click_ok:
            return ActionResult.success(Capability.CLICK_ELEMENT, message="Clicked")
        return ActionResult.failure(Capability.CLICK_ELEMENT,
                                    error=self.click_error or "boom", message="failed")

    async def send_keystroke(self, app, text, *, press_enter=False, task_id=None):
        self.actions.append(("type", text))
        return ActionResult.success(Capability.SEND_KEYSTROKE, message="Typed")

    async def key_combo(self, combo, *, app=None, task_id=None):
        self.actions.append(("key", combo))
        return ActionResult.success(Capability.KEY_COMBO, message="Done")

    async def open_app(self, app, *, task_id=None):
        self.actions.append(("open_app", app))
        return ActionResult.success(Capability.OPEN_APP, message="Opened")


class _AX:
    async def focus_element(self, ref):
        return True


class _Kill:
    def __init__(self, engaged=False):
        self._e = engaged

    def is_engaged(self):
        return self._e


def _no_capture(monkeypatch):
    async def none(app=None, max_dim=1366):
        return None
    monkeypatch.setattr(perception, "capture_focused_window", none)


def run(c):
    return asyncio.run(c)


def test_completes_done(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"click","ref":"e1"}', '{"action":"done","reason":"saved"}'])
    ex = FakeExec()
    emitted = []
    async def emit(k, t, detail="", status="active"): emitted.append((k, t))
    res = run(agent_loop.run_loop(ex, "save it", client, ax_executor=_AX(), emit=emit))
    assert res["status"] == "done"
    assert ("click", "e1") in ex.actions
    # observe + decide + act beats all streamed
    kinds = {k for k, _ in emitted}
    assert {"observe", "decide", "act"} <= kinds


def test_step_cap(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"click","ref":"e1"}'])  # never says done
    ex = FakeExec()
    res = run(agent_loop.run_loop(ex, "loop forever", client, max_steps=3, ax_executor=_AX()))
    assert res["status"] == "capped" and res["steps"] == 3
    assert len(ex.actions) == 3  # exactly the cap, no runaway


def test_veto_stops(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"click","ref":"e1"}'])
    ex = FakeExec(click_ok=False, click_error="denied")  # user denied the confirm
    res = run(agent_loop.run_loop(ex, "do it", client, max_steps=5, ax_executor=_AX()))
    assert res["status"] == "vetoed"
    assert len(ex.actions) == 1  # stopped after the veto, didn't keep going


def test_kill_switch_halts(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"click","ref":"e1"}'])
    ex = FakeExec()
    res = run(agent_loop.run_loop(ex, "do it", client, kill_switch=_Kill(engaged=True), ax_executor=_AX()))
    assert res["status"] == "halted"
    assert ex.actions == []  # never acted


def test_stuck_recovery_bails(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"click","ref":"e1"}'])
    ex = FakeExec(click_ok=False, click_error="boom")  # keeps erroring (not denied)
    res = run(agent_loop.run_loop(ex, "do it", client, max_steps=9, ax_executor=_AX()))
    assert res["status"] == "failed"
    assert len(ex.actions) == agent_loop._MAX_CONSECUTIVE_FAILS  # bailed, didn't loop blindly


def test_model_fail_is_honest(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"fail","reason":"no Save button here"}'])
    ex = FakeExec()
    res = run(agent_loop.run_loop(ex, "save", client, ax_executor=_AX()))
    assert res["status"] == "failed" and "Save" in res["message"]
    assert ex.actions == []


# ── Hybrid autonomy (hands_off) ─────────────────────────────────────────────

_SECURE = {"ref": "e3", "role": "AXSecureTextField", "title": "Password",
           "value": "", "enabled": True, "frame": [10, 100, 200, 24]}


def test_classify_step_tiers():
    obs = {"elements": ELS + [_SECURE]}
    # Safe click → auto.
    assert agent_loop._classify_step({"action": "click", "ref": "e1"}, obs) == "auto"
    # Credential field (click or type) → login handoff.
    assert agent_loop._classify_step({"action": "click", "ref": "e3"}, obs) == "login"
    assert agent_loop._classify_step({"action": "type", "ref": "e3", "text": "x"}, obs) == "login"
    # Destructive ⌘⌫ and destructive/payment wording → confirm.
    assert agent_loop._classify_step({"action": "key", "combo": "cmd+delete"}, obs) == "confirm"
    assert agent_loop._classify_step(
        {"action": "click", "ref": "e1", "reason": "delete the project"}, obs) == "confirm"
    assert agent_loop._classify_step(
        {"action": "click", "ref": "e1", "reason": "place order and pay"}, obs) == "confirm"


def test_hands_off_auto_routes_to_raw_executor(monkeypatch):
    # In hands_off mode a safe click runs through the RAW executor (no confirm
    # card). Route both roles to one recorder and assert it acted.
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"click","ref":"e1","reason":"the Save button"}',
                         '{"action":"done","reason":"saved"}'])
    ex = FakeExec()
    res = run(agent_loop.run_loop(ex, "save it", client, ax_executor=ex, hands_off=True))
    assert res["status"] == "done"
    assert ("click", "e1") in ex.actions


def test_hands_off_login_hands_off_then_resumes(monkeypatch):
    # Clicking a credential field hands off to the human; once the field clears,
    # the chain resumes and finishes. asyncio.sleep is stubbed so it's instant.
    _no_capture(monkeypatch)
    import asyncio as _aio
    async def _no_sleep(*a, **k): return None
    monkeypatch.setattr(_aio, "sleep", _no_sleep)

    class FakeExecLogin(FakeExec):
        def __init__(self):
            super().__init__()
            self.obs_calls = 0
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            self.obs_calls += 1
            # Login field present for the first two observations (initial + first
            # poll), then the user has signed in and it's gone.
            els = ELS + ([_SECURE] if self.obs_calls <= 2 else [])
            return ActionResult.success(Capability.OBSERVE_UI, data={"app": "App", "elements": els})

    client = FakeClient(['{"action":"click","ref":"e3","reason":"the password field"}',
                         '{"action":"done","reason":"appearance set"}'])
    ex = FakeExecLogin()
    emitted = []
    async def emit(k, t, detail="", status="active"): emitted.append((k, t))
    res = run(agent_loop.run_loop(ex, "log in then continue", client,
                                  ax_executor=ex, hands_off=True, emit=emit))
    assert res["status"] == "done"
    # Never clicked the secure field — VALET handed it off instead.
    assert ("click", "e3") not in ex.actions
    # Handed off the sign-in to the user.
    assert any(any(k in t.lower() for k in ("password", "login", "sign-in", "sign in"))
               for _, t in emitted)


def test_hands_off_login_timeout_pauses(monkeypatch):
    # If the login never clears, the loop pauses cleanly (no runaway, no act).
    _no_capture(monkeypatch)
    import asyncio as _aio
    async def _no_sleep(*a, **k): return None
    monkeypatch.setattr(_aio, "sleep", _no_sleep)

    class FakeExecStuck(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "App", "elements": ELS + [_SECURE]})

    client = FakeClient(['{"action":"click","ref":"e3","reason":"password"}'])
    ex = FakeExecStuck()
    res = run(agent_loop.run_loop(ex, "log in", client, ax_executor=ex, hands_off=True))
    assert res["status"] == "paused"
    assert ex.actions == []  # never acted on the credential field


# ── Gmail sign-in wall pause/resume (issue #284) ─────────────────────────────
_GMAIL_SIGNIN_ELS = [
    {"ref": "e0", "role": "AXWebArea", "title": "Sign in - Google Accounts", "value": "", "enabled": True, "frame": [0, 100, 800, 500]},
    {"ref": "e1", "role": "AXStaticText", "title": "to continue to Gmail", "value": "", "enabled": True, "frame": [10, 120, 300, 20]},
    {"ref": "e2", "role": "AXTextField", "title": "Email or phone", "value": "", "enabled": True, "frame": [10, 160, 300, 24]},
    {"ref": "e3", "role": "AXLink", "title": "Forgot email?", "value": "", "enabled": True, "frame": [10, 200, 120, 20]},
]
_GMAIL_INBOX_ELS = [
    {"ref": "e0", "role": "AXWebArea", "title": "Inbox - Gmail", "value": "", "enabled": True, "frame": [0, 100, 800, 500]},
    {"ref": "e1", "role": "AXButton", "title": "Compose", "value": "", "enabled": True, "frame": [10, 120, 100, 40]},
    {"ref": "e2", "role": "AXSearchField", "title": "Search mail", "value": "", "enabled": True, "frame": [200, 120, 400, 24]},
]


def test_gmail_signin_detected():
    assert agent_loop._is_gmail_signin({"app": "Google Chrome", "elements": _GMAIL_SIGNIN_ELS})
    # Signed IN (inbox present) is not a sign-in wall.
    assert not agent_loop._is_gmail_signin({"app": "Google Chrome", "elements": _GMAIL_INBOX_ELS})
    # Same sign-in text in a NON-browser app doesn't count (page-driven, browser-gated).
    assert not agent_loop._is_gmail_signin({"app": "App", "elements": _GMAIL_SIGNIN_ELS})


def test_gmail_signin_pauses_without_acting(monkeypatch):
    # A logged-out Gmail wall (email step, no chooser/password yet) pauses the loop
    # and hands off — never types creds. The message is returned for the caller to
    # speak (the loop itself no longer calls speak).
    _no_capture(monkeypatch)

    class FakeExecGmail(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": _GMAIL_SIGNIN_ELS})

    # The client would type into the email field if the loop ever decided — it must not.
    client = FakeClient(['{"action":"type","ref":"e2","text":"me@example.com","reason":"email"}'])
    ex = FakeExecGmail()
    res = run(agent_loop.run_loop(ex, "go to gmail.com and summarize today's emails",
                                  client, ax_executor=ex, hands_off=True))
    assert res["status"] == "paused" and res.get("reason") == "login"
    assert res["resume_goal"] == "go to gmail.com and summarize today's emails"
    assert ex.actions == []                     # never touched the credential field
    assert "signed out of gmail" in res["message"].lower()


_GMAIL_CHOOSER_ELS = [
    {"ref": "e0", "role": "AXWebArea", "title": "Choose an account", "value": "", "enabled": True, "frame": [0, 100, 800, 500]},
    {"ref": "e1", "role": "AXLink", "title": "Finley Underwood finley@qsbsrollover.com", "value": "", "enabled": True, "frame": [640, 160, 500, 50]},
    {"ref": "e2", "role": "AXLink", "title": "Finley Underwood mrfinleyunderwood@gmail.com", "value": "", "enabled": True, "frame": [640, 220, 500, 50]},
    {"ref": "e3", "role": "AXLink", "title": "Use another account", "value": "", "enabled": True, "frame": [640, 290, 500, 40]},
]
_GMAIL_PASSWORD_ELS = [
    {"ref": "e0", "role": "AXWebArea", "title": "Hi Finley", "value": "", "enabled": True, "frame": [0, 100, 800, 500]},
    {"ref": "e1", "role": "AXStaticText", "title": "finley@qsbsrollover.com", "value": "", "enabled": True, "frame": [60, 210, 220, 24]},
    {"ref": "e2", "role": "AXSecureTextField", "title": "Enter your password", "value": "", "enabled": True, "frame": [640, 210, 500, 30]},
    {"ref": "e3", "role": "AXButton", "title": "Next", "value": "", "enabled": True, "frame": [1080, 355, 75, 36]},
]


def test_gmail_account_chooser_asks_which(monkeypatch):
    # The chooser pauses to ASK which account (no click yet, no creds).
    _no_capture(monkeypatch)

    class FakeExecChooser(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": _GMAIL_CHOOSER_ELS})

    client = FakeClient(['{"action":"done","reason":"n/a"}'])
    ex = FakeExecChooser()
    res = run(agent_loop.run_loop(ex, "go to gmail and summarize today's emails",
                                  client, ax_executor=ex, hands_off=True))
    assert res["status"] == "paused" and res.get("reason") == "choose_account"
    assert res["accounts"] == ["finley@qsbsrollover.com", "mrfinleyunderwood@gmail.com"]
    assert ex.actions == []
    assert "which account" in res["message"].lower()


def test_gmail_password_page_asks_approval(monkeypatch):
    # The password step pauses to ASK approval, naming the account; no creds typed.
    _no_capture(monkeypatch)

    class FakeExecPw(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": _GMAIL_PASSWORD_ELS})

    client = FakeClient(['{"action":"type","ref":"e2","text":"hunter2","reason":"password"}'])
    ex = FakeExecPw()
    res = run(agent_loop.run_loop(ex, "go to gmail and summarize today's emails",
                                  client, ax_executor=ex, hands_off=True))
    assert res["status"] == "paused" and res.get("reason") == "approve_signin"
    assert res.get("account") == "finley@qsbsrollover.com"
    assert ex.actions == []                     # never typed into the secure field
    assert "saved password" in res["message"].lower()


def test_gmail_account_click_on_choice(monkeypatch):
    # Given the user's account choice, the loop clicks that row deterministically
    # off the AX tree (no vision guessing) and moves on — the screen flips to the
    # password page, so it then asks approval.
    _no_capture(monkeypatch)

    class FakeExecFlow(FakeExec):
        def __init__(self):
            super().__init__()
            self.obs_n = 0
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            self.obs_n += 1
            els = _GMAIL_CHOOSER_ELS if self.obs_n <= 1 else _GMAIL_PASSWORD_ELS
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": els})

    client = FakeClient(['{"action":"done","reason":"n/a"}'])
    ex = FakeExecFlow()
    res = run(agent_loop.run_loop(
        ex, "go to gmail and summarize today's emails", client, ax_executor=ex,
        hands_off=True, login_choice={"email": "finley@qsbsrollover.com"}))
    assert any(a[0] == "click" for a in ex.actions)   # clicked the chosen account
    assert res["status"] == "paused" and res.get("reason") == "approve_signin"


def test_find_account_element_is_exact():
    # The deterministic matcher must NOT confuse the two Finley accounts.
    obs = {"app": "Google Chrome", "elements": _GMAIL_CHOOSER_ELS}
    assert agent_loop._find_account_element(obs, "finley@qsbsrollover.com")["ref"] == "e1"
    assert agent_loop._find_account_element(obs, "mrfinleyunderwood@gmail.com")["ref"] == "e2"


def test_resolve_acct_hint():
    # A spoken account word maps onto the right chooser email so a named-account
    # digest ("summarize my WORK mail") auto-picks without re-asking.
    emails = ["finley@qsbsrollover.com", "mrfinleyunderwood@gmail.com"]
    assert agent_loop._resolve_acct_hint("work", emails) == "finley@qsbsrollover.com"
    assert agent_loop._resolve_acct_hint("business", emails) == "finley@qsbsrollover.com"
    assert agent_loop._resolve_acct_hint("personal", emails) == "mrfinleyunderwood@gmail.com"
    assert agent_loop._resolve_acct_hint("home", emails) == "mrfinleyunderwood@gmail.com"
    # A domain-stem fragment resolves the custom-domain (work) account.
    assert agent_loop._resolve_acct_hint("qsbs", emails) == "finley@qsbsrollover.com"
    # Nothing confident → "" (loop falls back to asking).
    assert agent_loop._resolve_acct_hint("", emails) == ""
    assert agent_loop._resolve_acct_hint("work", []) == ""


def test_gmail_acct_hint_auto_selects_without_asking(monkeypatch):
    # With acct_hint the loop resolves the named account against the chooser and
    # clicks it ITSELF — no "which account?" pause — then flips to the password
    # page and asks approval. This is the signed-out digest handoff path: the user
    # already named "work", so we don't ask again.
    _no_capture(monkeypatch)

    class FakeExecFlow(FakeExec):
        def __init__(self):
            super().__init__()
            self.obs_n = 0
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            self.obs_n += 1
            els = _GMAIL_CHOOSER_ELS if self.obs_n <= 1 else _GMAIL_PASSWORD_ELS
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": els})

    client = FakeClient(['{"action":"done","reason":"n/a"}'])
    ex = FakeExecFlow()
    res = run(agent_loop.run_loop(
        ex, "go to my work gmail and summarize July 18", client, ax_executor=ex,
        hands_off=True, acct_hint="work"))
    assert any(a[0] == "click" for a in ex.actions)     # auto-clicked the work account
    # Went straight to the password approval — never paused to ask which account.
    assert res["status"] == "paused" and res.get("reason") == "approve_signin"


def test_summary_goal_stops_at_inbox(monkeypatch):
    # With stop_at_gmail_inbox, reaching the inbox ends the loop (reason at_inbox)
    # so the caller can summarize — the generic click loop never runs.
    _no_capture(monkeypatch)

    class FakeExecInbox(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": _GMAIL_INBOX_ELS})

    # If the loop ever "decided", it would click — it must not for a summary goal.
    client = FakeClient(['{"action":"click","ref":"e1","reason":"x"}'])
    ex = FakeExecInbox()
    res = run(agent_loop.run_loop(ex, "go to gmail and summarize today's emails",
                                  client, ax_executor=ex, hands_off=True,
                                  stop_at_gmail_inbox=True))
    assert res["status"] == "done" and res.get("reason") == "at_inbox"
    assert ex.actions == []                     # never ran the generic click loop


def test_gmail_passkey_page_hands_off_without_looping(monkeypatch):
    # A passkey / "Verifying it's you" page can't be driven (biometric) — the loop
    # must pause ONCE and hand off, not loop through the old credential path.
    _no_capture(monkeypatch)
    passkey_els = [
        {"ref": "e0", "role": "AXWebArea", "title": "Verifying it's you", "value": "", "enabled": True, "frame": [0, 100, 800, 500]},
        {"ref": "e1", "role": "AXStaticText", "title": "Complete sign-in using your passkey", "value": "", "enabled": True, "frame": [60, 300, 400, 20]},
    ]

    class FakeExecPasskey(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": passkey_els})

    client = FakeClient(['{"action":"click","ref":"e1","reason":"x"}'])
    ex = FakeExecPasskey()
    res = run(agent_loop.run_loop(ex, "go to gmail and summarize today's emails",
                                  client, ax_executor=ex, hands_off=True))
    assert res["status"] == "paused" and res.get("reason") == "login"
    assert ex.actions == []                     # never tried to drive the passkey
    assert "fingerprint" in res["message"].lower()


def test_gmail_resume_when_signed_in(monkeypatch):
    # Once the inbox is present, the same loop proceeds (no re-pause).
    _no_capture(monkeypatch)

    class FakeExecInbox(FakeExec):
        async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
            return ActionResult.success(Capability.OBSERVE_UI,
                                        data={"app": "Google Chrome", "elements": _GMAIL_INBOX_ELS})

    client = FakeClient(['{"action":"done","reason":"inbox is loaded"}'])
    ex = FakeExecInbox()
    res = run(agent_loop.run_loop(ex, "go to gmail.com and summarize today's emails",
                                  client, ax_executor=ex, hands_off=True))
    assert res["status"] == "done"


if __name__ == "__main__":
    print("Run via pytest (uses monkeypatch).")
