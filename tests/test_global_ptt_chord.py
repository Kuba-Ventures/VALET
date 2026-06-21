"""Global PTT — ⌃⌥ chord state machine tests.

Exercises the pure ``_on_flags`` / ``_on_keydown`` helpers in ``global_ptt`` with
synthetic modifier/key sequences — no Quartz event tap needed. Asserts the chord
emits "down"/"up"/"cancel" in the right order so a ⌃⌥-letter shortcut never
dispatches as a voice command.

Run:  ./.venv/bin/python tests/test_global_ptt_chord.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import global_ptt


def _make():
    events = []
    ptt = global_ptt.GlobalPTT(lambda s: events.append(s))
    return ptt, events


def test_chord_engage_then_release():
    ptt, events = _make()
    # Control down first, then Option → chord engages on the second flag change.
    ptt._on_flags(control=True, option=False)
    assert events == []
    ptt._on_flags(control=True, option=True)
    assert events == ["down"]
    # Release Option → chord disengages, dispatch the turn.
    ptt._on_flags(control=True, option=False)
    assert events == ["down", "up"]


def test_option_first_then_control():
    ptt, events = _make()
    ptt._on_flags(control=False, option=True)
    assert events == []
    ptt._on_flags(control=True, option=True)
    assert events == ["down"]
    # Release both at once.
    ptt._on_flags(control=False, option=False)
    assert events == ["down", "up"]


def test_keydown_mid_hold_cancels():
    ptt, events = _make()
    ptt._on_flags(control=True, option=True)
    ptt._on_keydown()                       # a ⌃⌥-letter shortcut
    assert events == ["down", "cancel"]
    # Release after a cancel must NOT emit "up" (no dispatch).
    ptt._on_flags(control=False, option=False)
    assert events == ["down", "cancel"]


def test_keydown_cancels_only_once():
    ptt, events = _make()
    ptt._on_flags(control=True, option=True)
    ptt._on_keydown()
    ptt._on_keydown()                       # second key while still held
    assert events == ["down", "cancel"]     # cancel latches; no repeat


def test_option_only_never_fires():
    ptt, events = _make()
    ptt._on_flags(control=False, option=True)
    ptt._on_flags(control=False, option=False)
    assert events == []


def test_control_only_never_fires():
    ptt, events = _make()
    ptt._on_flags(control=True, option=False)
    ptt._on_flags(control=False, option=False)
    assert events == []


def test_keydown_outside_chord_is_ignored():
    ptt, events = _make()
    ptt._on_keydown()                       # typing with no chord held
    assert events == []


def test_other_modifier_change_while_held_does_not_retrigger():
    ptt, events = _make()
    ptt._on_flags(control=True, option=True)        # down
    # Shift pressed while ⌃⌥ stay held: flagsChanged fires again but the chord
    # bits are unchanged, so nothing re-triggers.
    ptt._on_flags(control=True, option=True)
    assert events == ["down"]
    ptt._on_flags(control=True, option=False)       # release Option
    assert events == ["down", "up"]


def test_full_cycle_resets_for_next_turn():
    ptt, events = _make()
    ptt._on_flags(control=True, option=True)
    ptt._on_flags(control=False, option=False)
    # Second turn works cleanly after the first.
    ptt._on_flags(control=True, option=True)
    ptt._on_flags(control=False, option=False)
    assert events == ["down", "up", "down", "up"]


def test_cancelled_turn_does_not_poison_next_turn():
    ptt, events = _make()
    ptt._on_flags(control=True, option=True)
    ptt._on_keydown()
    ptt._on_flags(control=False, option=False)      # cancelled, no "up"
    # Next clean turn must dispatch normally.
    ptt._on_flags(control=True, option=True)
    ptt._on_flags(control=False, option=False)
    assert events == ["down", "cancel", "down", "up"]


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
