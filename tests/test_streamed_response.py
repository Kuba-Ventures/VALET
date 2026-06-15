"""generate_response_streamed — headless integration tests.

Exercises the streaming-speak consumer with a fake Anthropic stream, a fake
WebSocket, and a stubbed synthesize_speech (no network, no API key). Verifies:
first-sentence-while-streaming, the (full_text, tail) contract, action-tag text
never being spoken, the barge-in should_cancel seam, and graceful TTS failure.

Run:  ./.venv/bin/python -m pytest tests/test_streamed_response.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import server


class _FakeStream:
    def __init__(self, deltas):
        self._deltas = deltas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def _gen():
            for d in self._deltas:
                yield d
        return _gen()

    def get_final_message(self):
        return object()


class _FakeMessages:
    def __init__(self, deltas):
        self._deltas = deltas

    def stream(self, **kwargs):
        return _FakeStream(self._deltas)


class _FakeClient:
    def __init__(self, deltas):
        self.messages = _FakeMessages(deltas)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


def _patch(monkeypatch, synth_ok=True):
    async def _noop_license():
        return None
    monkeypatch.setattr(server, "_ensure_license", _noop_license)
    monkeypatch.setattr(server, "assistant_blocked_message", lambda: "")
    monkeypatch.setattr(server, "_build_chat_request", lambda *a, **k: ([], []))
    monkeypatch.setattr(server, "track_usage", lambda *a, **k: None)

    async def _synth(text):
        return b"AUDIO" if synth_ok else None
    monkeypatch.setattr(server, "synthesize_speech", _synth)


def _run(deltas, ws, should_cancel=None):
    return asyncio.run(server.generate_response_streamed(
        "user text", _FakeClient(deltas), None, [], [],
        ws=ws, should_cancel=should_cancel,
    ))


def _audio(ws):
    return [m for m in ws.sent if m.get("type") == "audio"]


def test_speaks_first_sentence_then_returns_tail(monkeypatch):
    _patch(monkeypatch)
    ws = _FakeWS()
    full, tail = _run(["On it", ", sir. ", "The build ", "is starting"], ws)
    assert full == "On it, sir. The build is starting"
    assert tail == "The build is starting"          # unspoken remainder
    audio = _audio(ws)
    assert len(audio) == 1                            # only the completed sentence
    assert audio[0]["text"] == "On it, sir."
    assert any(m.get("state") == "speaking" for m in ws.sent)  # status sent before audio


def test_action_tag_text_is_never_spoken(monkeypatch):
    _patch(monkeypatch)
    ws = _FakeWS()
    full, tail = _run(["On it, sir. ", "[ACTION:BUILD] ", "a todo app"], ws)
    assert full == "On it, sir. [ACTION:BUILD] a todo app"
    assert tail == ""                                 # nothing speakable left before the tag
    audio = _audio(ws)
    assert len(audio) == 1 and audio[0]["text"] == "On it, sir."
    # The action target text must never have been synthesized/sent.
    assert all("ACTION" not in m.get("text", "") for m in audio)


def test_pure_action_speaks_nothing(monkeypatch):
    _patch(monkeypatch)
    ws = _FakeWS()
    full, tail = _run(["[ACTION:", "RESEARCH] ", "latest news"], ws)
    assert full == "[ACTION:RESEARCH] latest news"
    assert tail == ""
    assert _audio(ws) == []                           # no audio, no premature status churn


def test_single_sentence_is_tail_not_early_spoken(monkeypatch):
    # A 1-sentence reply has no trailing-space boundary, so it streams nothing
    # early and is returned as the tail (caller voices it) — matches the
    # non-streaming path, no regression.
    _patch(monkeypatch)
    ws = _FakeWS()
    full, tail = _run(["It's just ", "past nine, sir."], ws)
    assert full == "It's just past nine, sir."
    assert tail == "It's just past nine, sir."
    assert _audio(ws) == []


def test_should_cancel_stops_streaming(monkeypatch):
    _patch(monkeypatch)
    ws = _FakeWS()
    # Cancel immediately → no sentences spoken; full text reflects only what was
    # consumed before the break (nothing, since we check before first feed).
    full, tail = _run(["Hello. ", "World. "], ws, should_cancel=lambda: True)
    assert _audio(ws) == []
    assert full == ""


def test_tts_failure_sends_no_audio_but_returns_text(monkeypatch):
    _patch(monkeypatch, synth_ok=False)
    ws = _FakeWS()
    full, tail = _run(["All good. ", "Carry on"], ws)
    assert full == "All good. Carry on"
    assert tail == "Carry on"
    assert _audio(ws) == []                           # synth returned None → nothing sent


def test_license_block_returns_message_as_tail(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(server, "assistant_blocked_message", lambda: "Not licensed, sir.")
    ws = _FakeWS()
    full, tail = _run(["ignored"], ws)
    assert full == "Not licensed, sir." and tail == "Not licensed, sir."
    assert _audio(ws) == []                           # caller voices it via the tail path
