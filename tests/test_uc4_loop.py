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


if __name__ == "__main__":
    print("Run via pytest (uses monkeypatch).")
