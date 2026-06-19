"""Point-and-teach (UC3 locate-only) — headless unit tests.

Covers the voice-first reframe: "where is X" / "show me X" routes to a LOCATE
intent (never a click), the coarse spatial phrasing is correct, and — critically
— the locate path is structurally input-free (no click/focus/keystroke ever),
which is the trust guarantee point-and-teach rests on.

Run:  ./.venv/bin/python tests/test_uc3_point.py
"""

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import server


def test_where_is_routes_to_point():
    for phrase in ("where is the send button", "where's the inbox",
                   "show me the reply button", "show me where the menu is",
                   "point at the search bar", "find me the close button"):
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "ui_act" and a["ui_action"] == "point", (phrase, a)
        assert a["target"], phrase


def test_click_still_acts_not_points():
    a = server.detect_action_fast("click the submit button")
    assert a["action"] == "ui_act" and a["ui_action"] == "click"


def test_target_strips_trailing_button():
    a = server.detect_action_fast("where is the send button")
    assert a["target"] == "send"


def test_long_target_does_not_route():
    # Guard the 6-word cap so a rambling sentence stays conversational.
    a = server.detect_action_fast("show me the thing that I was working on earlier today please")
    assert a is None or a.get("ui_action") != "point"


def test_spatial_phrase_quadrants():
    win = [0, 0, 800, 600]
    assert server._spatial_phrase(60, 50, win) == "near the top-left"
    assert server._spatial_phrase(760, 50, win) == "near the top-right"
    assert server._spatial_phrase(60, 560, win) == "near the bottom-left"
    assert server._spatial_phrase(760, 560, win) == "near the bottom-right"
    assert server._spatial_phrase(400, 300, win) == "right in the centre"
    assert server._spatial_phrase(400, 50, win) == "along the top"
    assert server._spatial_phrase(60, 300, win) == "over on the left"


def test_spatial_phrase_no_window_is_graceful():
    assert server._spatial_phrase(100, 100, None) == "on screen"
    assert server._spatial_phrase(100, 100, [0, 0, 0, 0]) == "on screen"


def test_point_path_is_input_free():
    # The trust guarantee: the locate path must never synthesize input. If a future
    # edit wires a click/focus/keystroke into it, this fails loudly.
    # Strip the docstring so its prose ("never calls click_element ...") doesn't
    # trip the check; then look for the CALL form `name(`.
    src = inspect.getsource(server._resolve_and_point)
    doc = server._resolve_and_point.__doc__ or ""
    body = src.replace(doc, "")
    for forbidden in ("click_element(", "focus_element(", "send_keystroke(", "_mouse_click("):
        assert forbidden not in body, f"_resolve_and_point must not call {forbidden}"


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
