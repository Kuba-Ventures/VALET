"""Streaming sentence-chunking for the voice turn (perceived-latency PR 1).

When the model reply is streamed token-by-token, we want to start Fish TTS on
the first *complete sentence* as soon as it lands — rather than waiting for the
whole reply — so audio begins while the rest is still generating.

``SpeakableStreamer`` accumulates streamed deltas and hands back complete
sentences to speak as they finalize. Two invariants make this safe:

1. **Action tags are never spoken.** VALET embeds actions as ``[ACTION:TYPE] …``
   and ``extract_action`` treats everything before the tag as the spoken text
   (``clean_text = response[:match.start()]``). So the streamer stops emitting at
   the first ``[ACTION`` marker; the tag and its target stay unspoken and are
   handled by the existing post-stream path.
2. **Exact reconstruction.** Each emitted sentence includes its trailing
   whitespace, so the concatenation of emitted sentences is an exact prefix of
   the accumulated text. ``spoken_len`` therefore indexes straight into ``full``,
   and ``tail()`` returns precisely the not-yet-spoken speakable remainder — no
   fuzzy string matching, no risk of dropping or double-speaking text.

This module is pure (no network, no event loop), so it is covered by headless
unit tests (see ``tests/test_voice_stream.py``).
"""

from __future__ import annotations

import re

# A sentence ends at .!? (one or more) followed by whitespace. The whitespace is
# part of the match end, so a popped sentence carries its trailing space and the
# concatenation of sentences reproduces the consumed prefix byte-for-byte.
# Requiring trailing whitespace means "9.5" / "U.S." mid-token don't split, and a
# final sentence with no trailing whitespace is left for the post-stream tail.
_BOUNDARY = re.compile(r"[.!?]+\s+")

# The only bracket VALET emits in a reply is an action tag (`[ACTION:TYPE]`).
# Keying the cutoff on this exact marker (not a bare "[") means a literal bracket
# in conversational speech ("the list [a, b]") is still spoken.
_ACTION_MARKER = "[ACTION"


def pop_sentences(buf: str) -> tuple[list[str], str]:
    """Split ``buf`` into complete sentences + leftover.

    Each sentence INCLUDES its trailing whitespace, so ``"".join(sentences) +
    leftover == buf``. A trailing fragment with no sentence-final punctuation (or
    punctuation not followed by whitespace) stays in ``leftover``.
    """
    sentences: list[str] = []
    pos = 0
    for m in _BOUNDARY.finditer(buf):
        sentences.append(buf[pos:m.end()])
        pos = m.end()
    return sentences, buf[pos:]


class SpeakableStreamer:
    """Feeds in streamed text deltas; yields complete sentences to speak.

    Stops emitting at the first ``[ACTION`` marker so action-tag text is never
    spoken. ``tail()`` (call after the stream ends) returns the unspoken
    speakable remainder for the caller to synthesize last.
    """

    def __init__(self) -> None:
        self.full = ""        # everything received so far
        self.spoken_len = 0   # chars of the speakable region already emitted

    def _boundary(self) -> int:
        """Index where the speakable region ends — the first ``[ACTION`` marker,
        or the end of the accumulated text if none has appeared yet."""
        b = self.full.find(_ACTION_MARKER)
        return b if b != -1 else len(self.full)

    def feed(self, delta: str) -> list[str]:
        """Accumulate ``delta`` and return any newly-completed sentences to speak
        (each including trailing whitespace). Empty list if none completed."""
        self.full += delta
        region = self.full[self.spoken_len:self._boundary()]
        sentences, _leftover = pop_sentences(region)
        for s in sentences:
            self.spoken_len += len(s)
        return sentences

    def tail(self) -> str:
        """The not-yet-spoken speakable remainder (e.g. a final sentence with no
        trailing whitespace, or text before an action tag that never completed a
        sentence). Empty when everything speakable was already emitted."""
        return self.full[self.spoken_len:self._boundary()]
