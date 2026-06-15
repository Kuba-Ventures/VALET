"""Voice-turn latency instrumentation (perceived-latency work, PR 0).

A ``TurnTimer`` records monotonic timestamps at the four points that decide how
long the user waits between finishing speaking and hearing Vee reply:

    t0  speech_final   end-of-speech — the STT final transcript reaches the server
    t1  request_sent   the LLM request is about to go out to the proxy/model
    t2  first_token    the model response is available to the server
    t3  first_audio    the first TTS audio chunk is dispatched to the client

These four marks let every later latency change (streaming TTS, a faster Fish
tier, a pre-warmed proxy connection) be *measured* instead of guessed.

The timer holds ONLY mark names and timestamps — never transcript or payload
content — so logging or emitting a summary cannot leak what was said. This
mirrors the privacy-scrubbing discipline in ``observability.py``; there is
nothing to scrub here because no content ever enters the object.

The class is pure and has no dependency on the event bus or the network, so it
is exercised by headless unit tests (see ``tests/test_voice_timing.py``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# Ordered marks. Each segment is the delta from the previously recorded mark,
# so a turn that skips a mark (e.g. a canned reply that never hits the model)
# still yields a sensible speech_final -> first_audio total.
MARKS: tuple[str, ...] = ("speech_final", "request_sent", "first_token", "first_audio")


@dataclass
class TurnTimer:
    """Records voice-turn timing marks. Construction stamps ``speech_final``.

    ``clock`` is injectable purely so tests can drive a deterministic clock; in
    production it is ``time.perf_counter`` (monotonic, immune to wall-clock
    adjustments).
    """

    clock: Callable[[], float] = time.perf_counter
    _marks: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # t0: the timer is created the moment the final transcript arrives.
        self.mark("speech_final")

    def mark(self, name: str) -> None:
        """Record ``name`` at the current time. First write wins; later writes
        for the same mark (and unknown names) are ignored, so callers can mark
        defensively without corrupting an earlier reading."""
        if name in MARKS and name not in self._marks:
            self._marks[name] = self.clock()

    def recorded(self, name: str) -> bool:
        return name in self._marks

    def segments_ms(self) -> dict[str, float]:
        """Deltas between consecutive *recorded* marks, in milliseconds."""
        out: dict[str, float] = {}
        prev_name: str | None = None
        prev_t: float | None = None
        for name in MARKS:
            t = self._marks.get(name)
            if t is None:
                continue
            if prev_name is not None and prev_t is not None:
                out[f"{prev_name}->{name}"] = round((t - prev_t) * 1000, 1)
            prev_name, prev_t = name, t
        return out

    def total_ms(self) -> float | None:
        """End-to-end ms across all recorded marks, or ``None`` if fewer than
        two marks were recorded (nothing to span)."""
        present = [self._marks[m] for m in MARKS if m in self._marks]
        if len(present) < 2:
            return None
        return round((present[-1] - present[0]) * 1000, 1)

    def summary(self) -> str:
        """Human/log-friendly one-liner, e.g.
        ``speech_final->request_sent 4.2ms · request_sent->first_token 410.1ms ·
        first_token->first_audio 233.7ms · total 648.0ms``."""
        parts = [f"{seg} {ms}ms" for seg, ms in self.segments_ms().items()]
        total = self.total_ms()
        if total is not None:
            parts.append(f"total {total}ms")
        return " · ".join(parts) if parts else "no timing"
