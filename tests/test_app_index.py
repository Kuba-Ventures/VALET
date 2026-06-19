"""Voice console (Stage 1) — installed-app matcher + routing tests.

The matcher tests are pure (injected app list). The routing test imports server
and exercises detect_action_fast against the machine's real installed apps, so
it stays deterministic by picking an app that is actually present.

Run:  ./.venv/bin/python tests/test_app_index.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import app_index

APPS = ["Safari", "Google Chrome", "Visual Studio Code", "Slack", "Spotify",
        "System Settings", "Notes", "Terminal", "Notion"]


def test_exact_case_insensitive():
    assert app_index.match_app("Safari", APPS) == "Safari"
    assert app_index.match_app("safari", APPS) == "Safari"


def test_aliases():
    assert app_index.match_app("vs code", APPS) == "Visual Studio Code"
    assert app_index.match_app("the browser", APPS) == "Safari"
    assert app_index.match_app("system preferences", APPS) == "System Settings"


def test_prefix_and_substring():
    assert app_index.match_app("spot", APPS) == "Spotify"          # prefix
    assert app_index.match_app("studio", APPS) == "Visual Studio Code"  # substring


def test_fuzzy_handles_stt_slips():
    # Character-level slips (dropped/typo'd letters), which difflib catches.
    assert app_index.match_app("spotfy", APPS) == "Spotify"
    assert app_index.match_app("safri", APPS) == "Safari"


def test_the_prefix_stripped():
    assert app_index.match_app("the slack", APPS) == "Slack"


def test_terminal_is_reserved():
    # Terminal keeps its LLM OPEN_TERMINAL-vs-OPEN_APP routing.
    assert app_index.match_app("terminal", APPS) is None


def test_non_app_phrases_dont_match():
    assert app_index.match_app("a new tab", APPS) is None
    assert app_index.match_app("the pod bay doors", APPS) is None
    assert app_index.match_app("", APPS) is None


def test_real_scan_returns_some_apps():
    apps = app_index.installed_apps()
    assert isinstance(apps, list) and len(apps) > 0  # macOS always has some


def test_open_app_routes_against_real_apps():
    import server
    apps = app_index.installed_apps()
    assert apps, "no installed apps found on this machine"
    target = apps[0]
    a = server.detect_action_fast(f"open {target}")
    assert a and a["action"] == "open_app" and a["target"] == target, a
    # gibberish app name finds nothing → falls through (not open_app)
    b = server.detect_action_fast("open zxqwv nonexistent thingamajig")
    assert b is None or b.get("action") != "open_app", b
    # "open a new tab" must not hijack as an app launch
    c = server.detect_action_fast("open a new tab")
    assert c is None or c.get("action") != "open_app", c


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
