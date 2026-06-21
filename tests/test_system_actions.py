"""system_actions — spoken phrase → action spec + safety tier.

Run:  ./.venv/bin/python tests/test_system_actions.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import system_actions as sa


def test_lock_screen_tier0():
    spec = sa.match_action("lock the screen")
    assert spec and spec.name == "lock_screen"
    assert spec.tier == sa.TIER_SAFE


def test_volume_up_down_mute():
    assert sa.match_action("turn up the volume").name == "volume_up"
    assert sa.match_action("volume down").name == "volume_down"
    assert sa.match_action("mute").name == "mute"


def test_display_sleep_beats_sleep():
    # "sleep the display" must resolve to display_sleep, not the broader sleep.
    assert sa.match_action("sleep the display").name == "display_sleep"
    assert sa.match_action("go to sleep").name == "sleep"


def test_destructive_are_tier1():
    for phrase, name in [("empty the trash", "empty_trash"),
                         ("restart the mac", "restart"),
                         ("shut down", "shutdown")]:
        spec = sa.match_action(phrase)
        assert spec and spec.name == name, (phrase, spec)
        assert spec.tier == sa.TIER_DESTRUCTIVE, (phrase, spec.tier)


def test_mute_is_whole_word_only():
    # "commuter" must NOT trigger mute (whole-phrase matching).
    assert sa.match_action("open my commuter app") is None


def test_argv_is_list_form():
    for name, spec in sa._ACTIONS.items():
        assert isinstance(spec.argv, tuple) and spec.argv, name
        assert all(isinstance(a, str) for a in spec.argv), name


def test_unknown_returns_none():
    assert sa.match_action("what's the weather") is None
    assert sa.match_action("") is None


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
