"""Guided walkthrough (Stage 3) — pure logic + the teach loop with injected fakes.

No real screen, cursor, or LLM: run_walkthrough takes all I/O as callables, so we
drive it with scripted observations and collectors.

Run:  ./.venv/bin/python tests/test_walkthrough.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import walkthrough as wt


# ── pure: step_done ─────────────────────────────────────────────────────────
def test_step_done_verify_appears():
    before = {"app": "System Settings", "elements": []}
    after = {"app": "System Settings", "elements": [{"title": "Bluetooth"}]}
    assert wt.step_done(before, after, wt.Step("s", "n", verify="bluetooth")) is True


def test_step_done_verify_already_present_same_app_is_false():
    before = {"app": "Settings", "elements": [{"title": "Bluetooth"}]}
    after = {"app": "Settings", "elements": [{"title": "Bluetooth"}]}
    assert wt.step_done(before, after, wt.Step("s", "n", verify="bluetooth")) is False


def test_step_done_app_switch_no_verify():
    before = {"app": "Finder", "elements": []}
    after = {"app": "System Settings", "elements": []}
    assert wt.step_done(before, after, wt.Step("s", "n")) is True


def test_step_done_no_change_false():
    obs = {"app": "Finder", "elements": [{"title": "x"}]}
    assert wt.step_done(obs, dict(obs), wt.Step("s", "n")) is False


def test_step_done_change_when_verify_already_present():
    # "Dark" already labels the Appearance picker, so its presence can't mark
    # completion — but picking it changes other elements, which should count.
    before = {"app": "System Settings", "elements": [{"title": "Dark"}, {"title": "Light"}]}
    after = {"app": "System Settings",
             "elements": [{"title": "Dark"}, {"title": "Auto"}, {"title": "Tinted"}]}
    assert wt.step_done(before, after, wt.Step("s", "n", verify="dark")) is True


# ── pure: curated matching ───────────────────────────────────────────────────
def test_match_curated_bluetooth():
    steps = wt.match_curated("turn on bluetooth")
    assert steps and steps[0].title and steps[0].verify
    assert wt.match_curated("set up wi-fi")
    assert wt.match_curated("how to make a sandwich") is None


# ── plan_steps with a stub client (forced tool) ──────────────────────────────
class _Block:
    type = "tool_use"
    def __init__(self, steps): self.input = {"steps": steps}


class _Resp:
    def __init__(self, steps): self.content = [_Block(steps)]


class _StubMessages:
    def __init__(self, steps): self._steps = steps
    async def create(self, **kw): return _Resp(self._steps)


class _StubClient:
    def __init__(self, steps): self.messages = _StubMessages(steps)


def test_plan_steps_parses_tool_output():
    client = _StubClient([
        {"title": "Open it", "narration": "Open System Settings.", "target": "Settings", "verify": "general"},
        {"title": "", "narration": ""},  # dropped (no narration)
        {"narration": "Click the toggle."},
    ])
    steps = asyncio.run(wt.plan_steps("do a thing", client, {"app": "Finder"}))
    assert len(steps) == 2, steps
    assert steps[0].target == "Settings" and steps[0].verify == "general"
    assert steps[1].title  # backfilled from narration


# ── the teach loop with injected fakes ───────────────────────────────────────
class _Res:
    def __init__(self, frame=None, point=None, ref=None, label=""):
        self.frame, self.point, self.ref, self.label = frame, point, ref, label


def _fake_clock():
    t = {"v": 0.0}
    def clock():
        t["v"] += 5.0  # each call advances 5s (so timeout trips quickly)
        return t["v"]
    return clock


async def _noop_sleep(_): return None


def _run_loop(steps, observe, resolve, **over):
    spoken, glided, emitted = [], [], []
    deps = wt._LoopDeps(
        observe=observe,
        resolve=resolve,
        glide=lambda x, y, ref, label: glided.append((x, y, label)) or _aw(),
        speak=lambda t: spoken.append(t) or _aw(),
        emit=lambda title, detail="", status="active": emitted.append((title, status)) or _aw(),
        should_cancel=over.get("should_cancel", lambda: False),
        kill_engaged=over.get("kill_engaged", lambda: False),
        wait_signal=over.get("wait_signal", lambda: None),
        check_system=over.get("check_system"),
    )
    res = asyncio.run(wt.run_walkthrough(
        goal="g", steps=steps, deps=deps,
        poll_interval=0, step_timeout=1, clock=_fake_clock(), sleep=_noop_sleep))
    return res, spoken, glided, emitted


def _aw():
    async def _a(): return None
    return _a()


def test_loop_points_narrates_and_autoadvances():
    step = wt.Step("Toggle", "Flip the switch.", target="the switch", verify="on")
    state = {"n": 0}
    async def observe():
        state["n"] += 1
        # baseline empty; becomes "on" after the first re-observe → auto-advance
        return {"app": "Settings", "elements": ([{"title": "On"}] if state["n"] >= 2 else [])}
    async def resolve(obs, desc):
        return _Res(frame=[100, 100, 20, 20], ref="e1", label="the switch")
    res, spoken, glided, emitted = _run_loop([step], observe, resolve)
    assert res["status"] == "done", res
    assert glided and glided[0][0] == 110.0  # frame center x
    assert any("Flip the switch." in s for s in spoken), spoken


def test_loop_honest_miss_when_unresolved():
    step = wt.Step("Find it", "Look here.", target="the unicorn button", verify="z")
    async def observe(): return {"app": "Settings", "elements": [{"title": "z"}]}
    async def resolve(obs, desc): return _Res()  # no frame/point → miss
    res, spoken, glided, emitted = _run_loop([step], observe, resolve)
    assert not glided
    assert any("can't see" in s.lower() for s in spoken), spoken


def test_loop_completes_on_last_step_timeout():
    # If the last step's completion can't be detected, the loop wraps up as done
    # (so the caption + panel clear) instead of hanging — it still pointed first.
    step = wt.Step("Pick", "Click Dark.", target="the Dark option", verify="dark")
    async def observe(): return {"app": "Settings", "elements": [{"title": "Dark"}]}  # never changes
    async def resolve(obs, desc): return _Res(frame=[10, 10, 4, 4], ref="e1", label="the Dark option")
    res, spoken, glided, emitted = _run_loop([step], observe, resolve)
    assert res["status"] == "done", res
    assert glided


def test_loop_completes_via_system_check():
    # The Dark click leaves no detectable on-screen diff, but the reliable system
    # check (dark mode actually on) advances the step.
    step = wt.Step("Pick", "Click Dark.", target="the Dark option", verify="dark",
                   system_check="dark_mode_on")
    async def observe(): return {"app": "Settings", "elements": [{"title": "Dark"}]}
    async def resolve(obs, desc): return _Res(frame=[10, 10, 4, 4], ref="e1")
    calls = {"n": 0}
    async def check_system(key):
        calls["n"] += 1
        return key == "dark_mode_on"
    res, *_ = _run_loop([step], observe, resolve, check_system=check_system)
    assert res["status"] == "done", res
    assert calls["n"] >= 1  # the system-state check actually ran


def test_loop_captions_keyboard_step_without_target():
    # A keyboard step ("Press Command+Shift+P") has no control to glide to, but the
    # instruction should still ride by the cursor — set_caption fires for it.
    step = wt.Step("A", "Press Command+Shift+P.", target="")
    captioned = []
    async def observe(): return {"app": "X", "elements": []}
    deps = wt._LoopDeps(
        observe=observe, resolve=lambda o, d: _aw(),
        glide=lambda *a: _aw(), speak=lambda t: _aw(),
        emit=lambda *a, **k: _aw(),
        set_caption=lambda t: captioned.append(t) or _aw(),
    )
    asyncio.run(wt.run_walkthrough(
        goal="g", steps=[step], deps=deps,
        poll_interval=0, step_timeout=1, clock=_fake_clock(), sleep=_noop_sleep))
    assert captioned == ["Press Command+Shift+P."]


def test_loop_halts_on_kill():
    step = wt.Step("x", "n", target="t", verify="v")
    async def observe(): return {"app": "a", "elements": []}
    async def resolve(obs, desc): return _Res(frame=[0, 0, 2, 2])
    res, *_ = _run_loop([step], observe, resolve, kill_engaged=lambda: True)
    assert res["status"] == "halted", res


def test_loop_stop_signal_halts():
    step = wt.Step("x", "n", target="t", verify="never")
    async def observe(): return {"app": "a", "elements": []}
    async def resolve(obs, desc): return _Res(frame=[0, 0, 2, 2])
    res, *_ = _run_loop([step], observe, resolve, wait_signal=lambda: "stop")
    assert res["status"] == "halted", res


def test_empty_steps():
    res, *_ = _run_loop([], None, None) if False else (asyncio.run(wt.run_walkthrough(
        goal="g", steps=[], deps=wt._LoopDeps(
            observe=None, resolve=None, glide=None, speak=lambda t: _aw(),
            emit=lambda *a, **k: _aw()))), None, None, None)
    assert res["status"] == "empty"


def test_routing_walkthrough_vs_ui_act():
    import server
    for phrase in ["walk me through turning on bluetooth",
                   "show me how to set up filevault",
                   "teach me how to enable dark mode"]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "walkthrough" and a.get("goal"), (phrase, a)
    # point-and-teach must NOT be hijacked
    a = server.detect_action_fast("show me the trash")
    assert a and a["action"] == "ui_act", a


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
