"""Scrolling (issue #291) — routing, pixel maths, and the loop's scroll action.

VALET used to answer "scroll up" with "on it, sir" and then do nothing: there was
no scroll primitive anywhere, so the UI loop could only pick a click or key that
no-opped. These tests pin the whole path offline — no GUI, no API key, no Quartz.

Run:  ./.venv/bin/python -m pytest tests/test_scroll.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_loop
import perception
import safety
from action_executor import ActionResult, Capability

ELS = [
    {"ref": "e0", "role": "AXWindow", "title": "App", "value": "", "enabled": True,
     "frame": [0, 0, 800, 600]},
    {"ref": "e1", "role": "AXButton", "title": "Save", "value": "", "enabled": True,
     "frame": [10, 20, 80, 24]},
]


class _Resp:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class FakeClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.messages = self
        self.n = 0

    async def create(self, **kw):
        i = min(self.n, len(self.scripted) - 1)
        self.n += 1
        return _Resp(self.scripted[i])


class FakeExec:
    """Records scrolls. `moves` decides whether the view changes after one —
    the difference between a mid-page scroll and one at the bottom."""

    def __init__(self, moves=True):
        self.moves = moves
        self.actions = []
        self._offset = 0

    async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
        els = [dict(e, frame=[e["frame"][0], e["frame"][1] - self._offset,
                              e["frame"][2], e["frame"][3]]) for e in ELS]
        return ActionResult.success(Capability.OBSERVE_UI,
                                    data={"app": "Google Chrome", "elements": els})

    async def scroll(self, *, direction="down", amount="page", ref=None,
                     point=None, app=None, task_id=None):
        self.actions.append(("scroll", direction, amount, app))
        if self.moves:
            self._offset += 500
        return ActionResult.success(Capability.SCROLL, message="Scrolled, sir.")

    async def click_element(self, *, ref=None, point=None, app=None, task_id=None):
        self.actions.append(("click", ref))
        return ActionResult.success(Capability.CLICK_ELEMENT, message="Clicked")

    async def send_keystroke(self, app, text, *, press_enter=False, task_id=None):
        return ActionResult.success(Capability.SEND_KEYSTROKE, message="Typed")

    async def key_combo(self, combo, *, app=None, task_id=None):
        self.actions.append(("key", combo))
        return ActionResult.success(Capability.KEY_COMBO, message="Done")

    async def open_app(self, app, *, task_id=None):
        return ActionResult.success(Capability.OPEN_APP, message="Opened")

    async def focus_element(self, ref):
        return True

    async def glide_to_target(self, x, y, *, ref=None, duration=0.45, verify=True):
        return {"ok": True, "reason": None}




def _no_capture(monkeypatch):
    async def none(app=None, max_dim=1366):
        return None
    monkeypatch.setattr(perception, "capture_focused_window", none)


def run(c):
    return asyncio.run(c)


# ── pixel maths ─────────────────────────────────────────────────────────────
def test_scroll_pixels_by_amount():
    from accessibility_executor import _scroll_pixels
    # A page is most of the visible span, with overlap left so a search sweep
    # can't skip a target by landing it between two frames.
    assert 0.7 * 600 < _scroll_pixels("page", 600) < 600
    assert _scroll_pixels("half", 600) < _scroll_pixels("page", 600)
    assert _scroll_pixels("400", 600) == 400      # literal pixels win
    assert _scroll_pixels("40px", 600) == 40
    assert _scroll_pixels("page", 0) >= 200       # no window size → still moves


# ── the safety gate ─────────────────────────────────────────────────────────
def test_scroll_is_tier_zero():
    # Scrolling mutates nothing, so it must never pop a confirm card — otherwise
    # a "scroll until you find X" sweep asks for a tap per step.
    assert safety.classify(Capability.SCROLL).tier == safety.RiskTier.AUTO


def test_scroll_step_never_cards():
    d = {"action": "scroll", "direction": "down",
         "reason": "scroll down to the delete button"}   # destructive WORD, benign act
    assert agent_loop._classify_step(d, {"elements": ELS}) == "auto"


# ── the loop ────────────────────────────────────────────────────────────────
def test_loop_scrolls(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"scroll","direction":"down","amount":"page"}',
                         '{"action":"done","reason":"there"}'])
    ex = FakeExec()
    res = run(agent_loop.run_loop(ex, "scroll down", client, ax_executor=ex,
                                  hands_off=True))
    assert res["status"] == "done"
    assert ("scroll", "down", "page", "Google Chrome") in ex.actions


def test_loop_scroll_targets_the_observed_app(monkeypatch):
    # The `app` param can name a different app than the one actually focused; a
    # scroll sent there moves a window the user isn't looking at.
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"scroll","direction":"up"}',
                         '{"action":"done","reason":"ok"}'])
    ex = FakeExec()
    run(agent_loop.run_loop(ex, "scroll up", client, app="Safari",
                            ax_executor=ex, hands_off=True))
    assert ex.actions[0] == ("scroll", "up", "page", "Google Chrome")


def test_end_of_page_is_reported_to_the_model(monkeypatch):
    """A scroll at the bottom SUCCEEDS while moving nothing. Unless the loop
    notices, a search keeps scrolling a page that can't move."""
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"scroll","direction":"down"}'])
    ex = FakeExec(moves=False)
    res = run(agent_loop.run_loop(ex, "scroll until you find Atlantis", client,
                                  max_steps=2, ax_executor=ex, hands_off=True))
    assert "did not move" in res["history"][0]["msg"]
    assert "end of the page" in res["history"][0]["msg"]


def test_moving_scroll_is_not_flagged_as_the_end(monkeypatch):
    _no_capture(monkeypatch)
    client = FakeClient(['{"action":"scroll","direction":"down"}'])
    ex = FakeExec(moves=True)
    res = run(agent_loop.run_loop(ex, "scroll down", client, max_steps=1,
                                  ax_executor=ex, hands_off=True))
    assert "did not move" not in res["history"][0]["msg"]


def test_view_signature_tracks_position_not_just_labels():
    # A long list of identical rows scrolls without any label changing, so the
    # signature has to include geometry or end-of-page detection false-fires.
    same_labels_moved = [dict(e, frame=[e["frame"][0], e["frame"][1] - 300,
                                        e["frame"][2], e["frame"][3]]) for e in ELS]
    assert (agent_loop._view_signature({"elements": ELS})
            != agent_loop._view_signature({"elements": same_labels_moved}))


# ── voice routing ───────────────────────────────────────────────────────────
def test_router_plain_scroll():
    import server
    for text, want in [
        ("scroll up", ("up", "page")),
        ("scroll down", ("down", "page")),
        ("please scroll down a bit", ("down", "half")),
        ("scroll the page up", ("up", "page")),
        ("page down", ("down", "page")),
        ("scroll to the bottom", ("down", "20000")),
        ("scroll to the top", ("up", "20000")),
    ]:
        m = server._SCROLL_RE.match(text)
        assert m, f"{text!r} did not route to a scroll"
        assert server._scroll_params(m.group("dir"), m.group("amt")) == want, text


def test_router_scroll_until_is_a_search():
    import server
    m = server._SCROLL_FIND_RE.match("scroll down until you find the pricing table")
    assert m and m.group("target").strip() == "the pricing table"
    assert server._SCROLL_FIND_RE.match("keep scrolling until you see the footer")
    # …and a plain scroll must NOT be swallowed by the search pattern.
    assert not server._SCROLL_FIND_RE.match("scroll down")
