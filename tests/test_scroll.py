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


# ── window targeting ("scroll up on github") ────────────────────────────────
WINDOWS = [
    {"pid": 1, "app": "Cursor", "title": "ab_testing.py — VALET", "url": "",
     "frame": [6, 30, 1180, 900]},
    {"pid": 2, "app": "Slack", "title": "ev-wine-internal (Channel) - Kuba Ventures",
     "url": "", "frame": [1710, 30, 900, 1000]},
    {"pid": 3, "app": "Google Chrome", "url": "https://github.com/Kuba-Ventures/VALET/pull/317",
     "title": 'feat(control): scroll actions + "scroll until you find X" (#291) by kubatopia',
     "frame": [-1915, 56, 1856, 802]},
    {"pid": 3, "app": "Google Chrome", "url": "https://mail.google.com/mail/u/0/#inbox",
     "title": "Inbox (3) - finley@qsbsrollover.com - QSBS Rollover Mail",
     "frame": [-1916, 30, 1027, 819]},
    {"pid": 5, "app": "Messages", "title": "Pastor Faith", "url": "",
     "frame": [1710, 30, 800, 900]},
]


def test_target_resolves_by_site_not_title():
    """The whole reason URL matching exists: a GitHub PR window's title is
    'feat(control): … · Pull Request #317 · …' — the word 'github' appears
    nowhere in it. Title matching alone would miss the obvious case."""
    from accessibility_executor import find_window
    hit = find_window("github", WINDOWS)
    assert hit and hit["url"].startswith("https://github.com")
    assert "github" not in hit["title"].lower()   # the point of the test


def test_target_resolves_app_and_padding_words():
    from accessibility_executor import find_window
    assert find_window("slack", WINDOWS)["app"] == "Slack"
    # users pad targets: "the github page", "on the github tab"
    assert find_window("the github page", WINDOWS)["pid"] == 3
    assert find_window("gmail", WINDOWS)["url"].startswith("https://mail.google.com")


def test_unknown_target_returns_none_not_a_guess():
    """None means 'say so'. Falling back to the frontmost window is exactly the
    silent-wrong-window failure this feature exists to prevent."""
    from accessibility_executor import find_window
    assert find_window("spotify", WINDOWS) is None
    assert find_window("", WINDOWS) is None


def test_multi_monitor_frames_are_global():
    # Negative x = a display left of the main one. Nothing in the resolution
    # path may assume a single screen.
    from accessibility_executor import find_window
    assert find_window("github", WINDOWS)["frame"][0] < 0


def test_host_words_ignores_www_and_tld():
    from accessibility_executor import _host_words
    assert _host_words("https://www.github.com/x") == ["github"]
    assert _host_words("https://mail.google.com/mail/u/0") == ["mail", "google"]
    assert _host_words("not a url") == []


def test_scroll_with_unknown_target_fails_loudly(monkeypatch):
    """It must report the miss, not scroll something arbitrary."""
    import accessibility_executor as ax
    monkeypatch.setattr(ax, "_PYOBJC", True)
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "find_window", lambda t, c=None: None)
    moved = []
    monkeypatch.setattr(ax, "_scroll_wheel", lambda *a, **k: moved.append(a))
    res = run(ax.AccessibilityExecutor().scroll(direction="up", target="spotify"))
    assert not res.ok and res.error == "no_such_window"
    assert "spotify" in res.message.lower()
    assert moved == []          # nothing was scrolled


def test_named_target_does_not_steal_focus(monkeypatch):
    """A named window is scrolled where it sits. Raising it would yank the user
    off whatever they're doing on another display."""
    import accessibility_executor as ax
    monkeypatch.setattr(ax, "_PYOBJC", True)
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "find_window", lambda t, c=None: WINDOWS[2])
    activated, moved = [], []
    monkeypatch.setattr(ax, "_activate_app", lambda a: activated.append(a))
    monkeypatch.setattr(ax, "_scroll_wheel", lambda x, y, dy, dx=0, **k: moved.append((x, y, dy)))
    monkeypatch.setattr(ax, "_cursor_location", lambda: (500.0, 500.0))
    restored = []
    monkeypatch.setattr(ax, "_post_mouse_moved", lambda x, y: restored.append((x, y)))
    res = run(ax.AccessibilityExecutor().scroll(
        direction="down", target="github", app="Google Chrome"))
    assert res.ok
    assert activated == []                       # never raised
    assert restored == [(500.0, 500.0)]          # cursor put back
    x, y, dy = moved[0]
    assert x < 0 and dy < 0                      # left display, scrolled down


# ── voice routing ───────────────────────────────────────────────────────────
def test_router_extracts_the_window_target():
    import server
    for text, want_dir, want_target in [
        ("scroll up on github", "up", "github"),
        ("scroll down in slack", "down", "slack"),
        ("scroll up on the github page", "up", "github page"),   # leading "the" stripped
        ("scroll down a bit on github", "down", "github"),
        ("scroll down", "down", ""),
    ]:
        a = server.detect_action_fast(text)
        assert a and a.get("action") == "scroll", text
        assert a["direction"] == want_dir and a["target"] == want_target, text


def test_unrecognized_tail_is_not_silently_swallowed():
    """REGRESSION (the bug this change fixes): `amt` used to be a catch-all
    `[^.?!]*`, so "scroll up on github" matched as a plain scroll, the target
    was discarded, and VALET scrolled whatever was frontmost. An unparseable
    tail must now fail the match and fall through, never silently drop words."""
    import server
    a = server.detect_action_fast("scroll down and then delete everything")
    assert not (a or {}).get("action") == "scroll"


# ── amount before OR after the direction ────────────────────────────────────
def test_amount_can_precede_the_direction():
    """REGRESSION: "scroll all the way down" puts the amount BEFORE the
    direction. The pattern only accepted it after, so the whole match failed,
    the phrase fell through to the LLM, and VALET read out the Dow Jones."""
    import server
    for text in ("scroll all the way down", "scroll all the way down on github",
                 "scroll all the way up", "scroll way down"):
        a = server.detect_action_fast(text)
        assert a.get("action") == "scroll", f"{text!r} routed to {a.get('action')!r}"
        assert a["amount"] == "20000", text
    # and the post-direction phrasing still works
    assert server.detect_action_fast("scroll down all the way")["amount"] == "20000"


def test_to_the_end_amount_is_recognised_as_such():
    from accessibility_executor import _scroll_pixels, _TO_THE_END
    assert _scroll_pixels("20000", 800) >= _TO_THE_END      # triggers the end-jump
    assert _scroll_pixels("page", 800) < _TO_THE_END
    assert _scroll_pixels("half", 800) < _scroll_pixels("page", 800)


def test_all_the_way_prefers_the_scrollbar(monkeypatch):
    """A settable AXScrollBar is exact and instant. Spraying wheel events to
    reach the end of a long document is the slow fallback, not the plan."""
    import accessibility_executor as ax
    monkeypatch.setattr(ax, "_PYOBJC", True)
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "find_window", lambda t, c=None: WINDOWS[0])
    jumped, wheeled = [], []
    monkeypatch.setattr(ax, "_jump_scrollbar",
                        lambda pid, frame, to_end: jumped.append(to_end) or True)
    monkeypatch.setattr(ax, "_scroll_wheel", lambda *a, **k: wheeled.append(a))
    monkeypatch.setattr(ax, "_cursor_location", lambda: (0.0, 0.0))
    monkeypatch.setattr(ax, "_post_mouse_moved", lambda x, y: None)
    res = run(ax.AccessibilityExecutor().scroll(
        direction="down", amount="20000", target="cursor"))
    assert res.ok and jumped == [True] and wheeled == []


def test_all_the_way_falls_back_to_bursts_without_a_scrollbar(monkeypatch):
    """Chrome's web content exposes no scrollbar, so the end must still be
    reachable by repetition."""
    import accessibility_executor as ax
    monkeypatch.setattr(ax, "_PYOBJC", True)
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "find_window", lambda t, c=None: WINDOWS[2])
    monkeypatch.setattr(ax, "_jump_scrollbar", lambda *a, **k: False)
    wheeled = []
    monkeypatch.setattr(ax, "_scroll_wheel", lambda *a, **k: wheeled.append(a))
    monkeypatch.setattr(ax, "_cursor_location", lambda: (0.0, 0.0))
    monkeypatch.setattr(ax, "_post_mouse_moved", lambda x, y: None)
    res = run(ax.AccessibilityExecutor().scroll(
        direction="down", amount="20000", target="github"))
    assert res.ok
    assert len(wheeled) == ax._MAX_BURSTS      # keeps going, doesn't nudge once
    assert all(w[2] < 0 for w in wheeled)      # every burst scrolls DOWN


def test_page_and_half_scale_with_the_ask(monkeypatch):
    """A page must be more travel than a bit. They collapsed to the same fixed
    nudge when scroll distance was decided by a measurement that couldn't see
    text views move."""
    import accessibility_executor as ax
    monkeypatch.setattr(ax, "_PYOBJC", True)
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "find_window", lambda t, c=None: WINDOWS[2])
    monkeypatch.setattr(ax, "_jump_scrollbar", lambda *a, **k: False)
    monkeypatch.setattr(ax, "_cursor_location", lambda: (0.0, 0.0))
    monkeypatch.setattr(ax, "_post_mouse_moved", lambda x, y: None)

    def count(amount):
        w = []
        monkeypatch.setattr(ax, "_scroll_wheel", lambda *a, **k: w.append(a))
        run(ax.AccessibilityExecutor().scroll(
            direction="down", amount=amount, target="github"))
        return len(w)

    assert count("half") < count("page") < ax._MAX_BURSTS


# ── speech-recognition slips on window names ────────────────────────────────
def test_fuzzy_target_survives_mishearings():
    """The recognizer garbles names constantly. Exact-only matching turned every
    slip into "I can't see a <garbage> window, sir"."""
    from accessibility_executor import find_window
    for heard, want_app in [
        ("sleck", "Slack"), ("slock", "Slack"), ("zlack", "Slack"),
        ("kursor", "Cursor"), ("curser", "Cursor"),
        ("messeges", "Messages"),
    ]:
        hit = find_window(heard, WINDOWS)
        assert hit and hit["app"] == want_app, f"{heard!r} -> {hit and hit['app']}"


def test_known_substitutions_are_mapped():
    """"ghetto" is not a near-miss of "github" — it scores 0.33 on a string
    ratio, because the recognizer swapped in a different real word. Fuzzy can't
    reach it, so observed substitutions get an explicit table."""
    import difflib
    from accessibility_executor import find_window, _HEARD_AS
    assert difflib.SequenceMatcher(None, "ghetto", "github").ratio() < 0.5
    for heard in ("ghetto", "get hub", "gethub"):
        hit = find_window(heard, WINDOWS)
        assert hit and "github" in hit["url"], f"{heard!r} did not reach GitHub"
    assert _HEARD_AS["ghetto"] == "github"


def test_fuzzy_still_refuses_a_bad_guess():
    """A wrong window is worse than an honest miss — the cutoff must hold."""
    from accessibility_executor import find_window
    assert find_window("spotify", WINDOWS) is None
    assert find_window("xyzzy", WINDOWS) is None
    assert find_window("photoshop", WINDOWS) is None


# ── reaching an end without saying "scroll" ─────────────────────────────────
def test_go_to_the_top_is_a_scroll_not_a_click():
    """REGRESSION: "go to the top of GitHub" fell through to the click resolver,
    which tried to click a control literally named "top" — the process panel
    showed "CLICK TOP OF GITHUB" and nothing happened."""
    import server
    for text, want_dir, want_target in [
        ("go to the top", "up", ""),
        ("go to the bottom", "down", ""),
        ("go to the top of github", "up", "github"),
        ("jump to the bottom", "down", ""),
        ("take me to the top of github", "up", "github"),
        ("scroll to bottom", "down", ""),          # no "the"
        ("scroll to the bottom of github", "down", "github"),   # "of", not "on"
    ]:
        a = server.detect_action_fast(text) or {}
        assert a.get("action") == "scroll", f"{text!r} routed to {a.get('action')!r}"
        assert a["direction"] == want_dir and a["target"] == want_target, text
        assert a["amount"] == "20000", text       # an end, not a page


def test_asking_whats_there_scrolls_and_reports():
    """"See what's at the bottom" is about the CONTENT — go there, then read it
    back. Scrolling silently would answer the wrong question."""
    import server
    for text in ("see whats at the bottom", "what's at the bottom of github",
                 "show me the top of github", "what is at the top"):
        a = server.detect_action_fast(text) or {}
        assert a.get("action") == "scroll", f"{text!r} routed to {a.get('action')!r}"
        assert a.get("then") == "summarize", text


def test_end_phrasings_do_not_swallow_neighbouring_routes():
    """These patterns sit in front of the generic nav/click rules, so they must
    stay narrow — "go to <somewhere>" is still navigation."""
    import server
    for text, not_scroll in [
        ("go to my inbox", "ui_act"),
        ("go to github", "open_url"),
        ("click the top button", "ui_act"),
        ("whats on my screen", "describe_screen"),
        ("summarize this", "summarize_screen"),
    ]:
        a = server.detect_action_fast(text) or {}
        assert a.get("action") == not_scroll, f"{text!r} became {a.get('action')!r}"


def test_router_scroll_until_is_a_search():
    import server
    m = server._SCROLL_FIND_RE.match("scroll down until you find the pricing table")
    assert m and m.group("target").strip() == "the pricing table"
    assert server._SCROLL_FIND_RE.match("keep scrolling until you see the footer")
    # …and a plain scroll must NOT be swallowed by the search pattern.
    assert not server._SCROLL_FIND_RE.match("scroll down")


# ── product names the recognizer swaps for other words ──────────────────────
def test_misheard_product_names_are_fixed_before_routing():
    """REGRESSION: "go to get hub" reached the CLICK resolver hunting for a
    control named "get hub", while "go to github" opened the site correctly.
    Correcting the transcript fixes every path at once — nav, click and scroll —
    rather than one feature's lookup table."""
    import voice_text, server
    for heard, want_action in [
        ("go to get hub", "open_url"),
        ("open get hub", "open_url"),
        ("scroll down on get hub", "scroll"),
        ("go to the bottom of the get hub page", "scroll"),
    ]:
        corrected = voice_text.apply_speech_corrections(heard)
        assert "GitHub" in corrected, f"{heard!r} -> {corrected!r}"
        a = server.detect_action_fast(corrected) or {}
        assert a.get("action") == want_action, f"{heard!r} -> {a.get('action')!r}"


def test_ghetto_is_only_corrected_where_it_means_github():
    """"ghetto" is ordinary English. Correcting it everywhere would corrupt
    normal speech, so it is only fixed after a preposition or before a
    site-shaped noun."""
    import voice_text
    assert "GitHub" in voice_text.apply_speech_corrections("scroll down on ghetto")
    assert "GitHub" in voice_text.apply_speech_corrections("go to the ghetto page")
    # left alone in ordinary use
    untouched = voice_text.apply_speech_corrections("the ghetto is a real word")
    assert "GitHub" not in untouched and "ghetto" in untouched


def test_ambiguous_words_are_not_globally_substituted():
    """A wrong global substitution corrupts every downstream path at once, so
    real words with real uses stay out of the transcript-level table."""
    import voice_text
    out = voice_text.apply_speech_corrections("upload it to the cloud and slap a label on it")
    assert "slack" not in out.lower()      # "slap" is a real word
