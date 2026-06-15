"""Voice-turn timing — headless unit tests.

No API key, audio, or network required. Covers the TurnTimer mark/segment/total
arithmetic, first-write-wins semantics, skipped-mark handling, and the
privacy-by-construction guarantee (no transcript content ever stored).

Run:  ./.venv/bin/python -m pytest tests/test_voice_timing.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_timing import MARKS, TurnTimer


class _FakeClock:
    """Deterministic monotonic clock driven by an explicit list of readings."""

    def __init__(self, readings):
        self._readings = list(readings)
        self._i = 0

    def __call__(self):
        v = self._readings[self._i]
        self._i += 1
        return v


def test_construction_stamps_speech_final():
    timer = TurnTimer(clock=_FakeClock([10.0]))
    assert timer.recorded("speech_final")
    assert not timer.recorded("request_sent")
    # Only one mark → nothing to span.
    assert timer.total_ms() is None
    assert timer.segments_ms() == {}
    assert timer.summary() == "no timing"


def test_full_turn_segments_and_total():
    # speech_final=1.0, request_sent=1.004, first_token=1.414, first_audio=1.654
    timer = TurnTimer(clock=_FakeClock([1.0, 1.004, 1.414, 1.654]))
    timer.mark("request_sent")
    timer.mark("first_token")
    timer.mark("first_audio")

    segs = timer.segments_ms()
    assert segs == {
        "speech_final->request_sent": 4.0,
        "request_sent->first_token": 410.0,
        "first_token->first_audio": 240.0,
    }
    assert timer.total_ms() == 654.0
    assert "total 654.0ms" in timer.summary()


def test_first_write_wins():
    timer = TurnTimer(clock=_FakeClock([1.0, 2.0, 9.0]))
    timer.mark("request_sent")  # records 2.0
    timer.mark("request_sent")  # ignored — clock would have returned 9.0
    assert timer.segments_ms()["speech_final->request_sent"] == 1000.0


def test_unknown_mark_ignored():
    timer = TurnTimer(clock=_FakeClock([1.0]))
    timer.mark("not_a_real_mark")
    assert timer.segments_ms() == {}
    assert timer.total_ms() is None


def test_skipped_mark_spans_to_next_recorded():
    # A canned reply never hits the model: only speech_final + first_audio.
    timer = TurnTimer(clock=_FakeClock([1.0, 1.120]))
    timer.mark("first_audio")
    segs = timer.segments_ms()
    # No request_sent / first_token → the only segment spans speech_final→first_audio.
    assert segs == {"speech_final->first_audio": 120.0}
    assert timer.total_ms() == 120.0


def test_marks_order_is_stable():
    assert MARKS == ("speech_final", "request_sent", "first_token", "first_audio")


def test_holds_only_names_and_floats():
    """Privacy by construction: the timer's only state is mark-name → float."""
    timer = TurnTimer(clock=_FakeClock([1.0, 1.2, 1.5, 1.9]))
    timer.mark("request_sent")
    timer.mark("first_token")
    timer.mark("first_audio")
    assert set(timer._marks.keys()) <= set(MARKS)
    assert all(isinstance(v, float) for v in timer._marks.values())
