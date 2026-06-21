"""Stage 2 routing — detect_action_fast resolves files / settings / system
actions on the no-LLM fast path (and doesn't hijack app/conversation phrases).

Imports server (heavy) and exercises the pure sync router; no mdfind runs here
(detect returns a find_file action; the executor does the search).

Run:  ./.venv/bin/python tests/test_stage2_routing.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import server


def test_system_action_lock_tier0():
    a = server.detect_action_fast("lock the screen")
    assert a and a["action"] == "system_action" and a["name"] == "lock_screen", a
    assert a["tier"] == 0, a


def test_system_action_empty_trash_tier1():
    a = server.detect_action_fast("empty the trash")
    assert a and a["action"] == "system_action" and a["name"] == "empty_trash", a
    assert a["tier"] == 1, a


def test_settings_bluetooth():
    a = server.detect_action_fast("go to bluetooth settings")
    assert a and a["action"] == "open_settings" and a["label"] == "Bluetooth", a
    assert a["target"].startswith("x-apple.systempreferences:"), a


def test_settings_displays():
    a = server.detect_action_fast("open display settings")
    assert a and a["action"] == "open_settings" and a["label"] == "Displays", a


def test_find_file_browse_verb():
    a = server.detect_action_fast("find my q2 report")
    assert a and a["action"] == "find_file", a
    assert a["query"] == "q2 report", a


def test_open_with_kind_cue_is_file():
    a = server.detect_action_fast("open my budget spreadsheet")
    assert a and a["action"] == "find_file", a
    assert a["kind"] == "spreadsheet", a


def test_open_plain_phrase_not_hijacked():
    # No app, no file cue → must NOT become a file search (falls to LLM).
    a = server.detect_action_fast("open the pod bay doors")
    assert a is None or a["action"] not in ("find_file", "open_settings", "system_action"), a


def test_home_folder_opens():
    for phrase, target in [("open my downloads folder", "Downloads"),
                           ("open desktop", "Desktop"),
                           ("open my documents", "Documents")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "open_app" and a["target"] == target, (phrase, a)


def test_open_known_app_still_wins():
    # An installed app must beat the file fallback.
    a = server.detect_action_fast("open safari")
    assert a and a["action"] == "open_app", a


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
