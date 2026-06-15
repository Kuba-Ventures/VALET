"""Streaming sentence-chunking — headless unit tests.

No API key or network. Covers pop_sentences reconstruction, multi-sentence
early-emit, the [ACTION marker cutoff, literal-bracket safety, exact tail
computation, and delta-by-delta feeding (simulating a token stream).

Run:  ./.venv/bin/python -m pytest tests/test_voice_stream.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_stream import SpeakableStreamer, pop_sentences


def test_pop_sentences_exact_reconstruction():
    buf = "One. Two!  Three? leftover"
    sentences, leftover = pop_sentences(buf)
    assert sentences == ["One. ", "Two!  ", "Three? "]
    assert leftover == "leftover"
    assert "".join(sentences) + leftover == buf


def test_pop_sentences_no_split_without_trailing_space():
    # Final sentence with no trailing whitespace stays as leftover.
    sentences, leftover = pop_sentences("Hello world.")
    assert sentences == []
    assert leftover == "Hello world."


def test_decimal_and_no_space_not_split():
    sentences, leftover = pop_sentences("It costs 9.5 dollars and 3.14 each")
    assert sentences == []
    assert leftover == "It costs 9.5 dollars and 3.14 each"


def _feed_all(text: str, chunk: int = 3):
    """Feed text to a streamer in fixed-size chunks (simulates token deltas)."""
    s = SpeakableStreamer()
    spoken: list[str] = []
    for i in range(0, len(text), chunk):
        spoken.extend(s.feed(text[i:i + chunk]))
    return s, spoken


def test_multi_sentence_streams_early_then_tail():
    text = "On it, sir. The build is starting now"
    s, spoken = _feed_all(text)
    # First sentence emitted as soon as ". " arrives; second has no trailing
    # punctuation+space so it lands in the tail.
    assert spoken == ["On it, sir. "]
    assert s.tail() == "The build is starting now"
    # Nothing dropped, nothing duplicated.
    assert "".join(spoken) + s.tail() == text


def test_action_marker_is_never_spoken():
    text = "On it, sir. [ACTION:BUILD] a todo app with auth"
    s, spoken = _feed_all(text)
    assert spoken == ["On it, sir. "]
    # Tail is only the speakable remainder before the tag (here: empty — the
    # only sentence completed and the rest is the action).
    assert s.tail() == ""
    assert s.full == text  # full text still captured for extract_action


def test_pure_action_speaks_nothing():
    text = "[ACTION:RESEARCH] latest SEC ETF rulings"
    s, spoken = _feed_all(text)
    assert spoken == []
    assert s.tail() == ""  # nothing before the marker


def test_speakable_prefix_before_action_with_partial_tail():
    text = "Sure. Opening that now [ACTION:OPEN_APP] Slack"
    s, spoken = _feed_all(text)
    assert spoken == ["Sure. "]
    # "Opening that now " is speakable (before the tag) but never completed a
    # sentence, so it's the tail.
    assert s.tail() == "Opening that now "


def test_literal_bracket_in_speech_is_still_speakable():
    # A bare "[" that isn't an action tag must NOT cut off speech.
    text = "The list is [a, b, c] and that's all."
    s = SpeakableStreamer()
    spoken = s.feed(text)
    # No "[ACTION" marker → whole thing is speakable; the final sentence has a
    # trailing period but no trailing whitespace, so it lands in the tail.
    assert spoken == []
    assert s.tail() == text


def test_single_sentence_is_all_tail():
    # Common 1-sentence reply: no early emit (matches non-streaming behaviour),
    # spoken via the tail after the stream completes.
    s, spoken = _feed_all("It's just past nine, sir.")
    assert spoken == []
    assert s.tail() == "It's just past nine, sir."


def test_concatenation_invariant_holds_across_feeds():
    text = "First sentence here. Second one too! And a third? Then a tail"
    s, spoken = _feed_all(text, chunk=1)  # one char at a time
    assert spoken == ["First sentence here. ", "Second one too! ", "And a third? "]
    assert s.tail() == "Then a tail"
    assert "".join(spoken) + s.tail() == text
