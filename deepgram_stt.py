"""Deepgram Nova streaming speech-to-text.

WebKit's `webkitSpeechRecognition` (the recognizer VALET shipped with) has two
limits that matter for a real user: it is tuned for unaccented en-US, and it
cannot be told which words you actually say. That combination is how "GitHub"
came back as "ghetto" — not a garbled near-miss but a confident substitution of
a commoner English word.

Deepgram fixes both halves:

  * **Accent robustness.** Nova is materially better on accented English, which
    is the whole point for a French-speaking user.
  * **Keyword boosting.** `keyterm` biases the model toward words we KNOW are
    in play. VALET has an unusual advantage here: it can see which windows are
    open, so the vocabulary is boosted toward the things the user could
    plausibly be talking about right now (see `keyterms_for_context`).

Transport: the backend holds the Deepgram socket, not the frontend. The frontend
streams raw PCM to VALET and gets transcripts back, so the API key never leaves
the machine and the browser needs no credentials.

Audio contract: 16 kHz, mono, signed 16-bit little-endian PCM.

With no `DEEPGRAM_API_KEY` set, `available()` is False and the caller keeps using
the built-in recognizer — a missing key degrades quality, never voice itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator, Callable, Optional

log = logging.getLogger("valet.stt")

_ENDPOINT = "wss://api.deepgram.com/v1/listen"

SAMPLE_RATE = 16000
ENCODING = "linear16"
CHANNELS = 1

# Words VALET hears constantly that a general model gets wrong. The wake name is
# here because "Vee" is one syllable and collides with the/be/see; the rest are
# product and site names that a general English model has no reason to prefer.
_DOMAIN_TERMS = [
    "Vee", "VALET", "GitHub", "Slack", "Gmail", "Cursor", "Claude", "Finder",
    "Messages", "Notion", "Xcode", "Figma", "Linear", "Vercel", "Supabase",
    "Stripe", "Calendar", "Reminders", "Safari", "Chrome", "Terminal",
]

# Deepgram caps how many keyterms it will take; keep the budget for the ones
# that actually disambiguate rather than sending every window title verbatim.
_MAX_KEYTERMS = 40


def available() -> bool:
    """True when a Deepgram key is configured. False means: keep using WebKit."""
    return bool((os.getenv("DEEPGRAM_API_KEY") or "").strip())


def keyterms_for_context(windows: Optional[list] = None) -> list:
    """Domain vocabulary plus the names of whatever is on screen right now.

    The context half is the interesting part. A general recognizer has no reason
    to prefer "GitHub" over "ghetto" — but if a GitHub window is open, that is
    overwhelmingly the more likely word, and boosting it fixes the error at the
    source instead of correcting it afterwards."""
    terms = list(_DOMAIN_TERMS)
    for c in (windows or []):
        app = (c.get("app") or "").strip()
        if app and app not in terms:
            terms.append(app)
        # Site names ("github", "gmail") — the way people name browser windows.
        url = c.get("url") or ""
        try:
            import accessibility_executor as _ax
            for host in _ax._host_words(url):
                h = host.strip()
                if len(h) > 2 and h.lower() not in {t.lower() for t in terms}:
                    terms.append(h)
        except Exception:
            pass
    return terms[:_MAX_KEYTERMS]


def _build_url(keyterms: list, language: str = "en-US", model: str = "nova-3") -> str:
    from urllib.parse import urlencode
    params = [
        ("model", model),
        ("language", language),
        ("encoding", ENCODING),
        ("sample_rate", str(SAMPLE_RATE)),
        ("channels", str(CHANNELS)),
        ("interim_results", "true"),
        ("punctuate", "true"),
        ("smart_format", "true"),
        # Close an utterance after a short pause so commands feel immediate
        # rather than waiting for a long silence timeout.
        ("endpointing", "300"),
    ]
    # `keyterm` is nova-3's keyword boosting and is repeatable. On nova-2 the
    # equivalent is `keywords`; both were verified against the live service.
    key = "keyterm" if model.startswith("nova-3") else "keywords"
    params += [(key, t) for t in keyterms]
    return f"{_ENDPOINT}?{urlencode(params)}"


class DeepgramSession:
    """One live transcription session.

    `feed()` pushes PCM in, `transcripts()` yields {text, is_final} out. The two
    run concurrently — the caller pumps audio from its own socket while reading
    results, so partial transcripts arrive while the user is still speaking."""

    def __init__(self, keyterms: Optional[list] = None, language: str = "en-US",
                 model: str = "nova-3"):
        self._url = _build_url(keyterms or keyterms_for_context(), language, model)
        self._key = (os.getenv("DEEPGRAM_API_KEY") or "").strip()
        self._ws = None
        self._closed = False

    async def __aenter__(self):
        import websockets
        self._ws = await websockets.connect(
            self._url, additional_headers={"Authorization": f"Token {self._key}"},
            max_size=None, ping_interval=5, ping_timeout=20,
        )
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def feed(self, pcm: bytes) -> None:
        if self._ws is not None and not self._closed and pcm:
            await self._ws.send(pcm)

    async def finish(self) -> None:
        """Tell Deepgram no more audio is coming, but KEEP the socket open.

        Closing here instead would cut the connection before the final results
        come back — the transcript arrives after CloseStream, not before it."""
        if self._ws is None or self._closed:
            return
        self._closed = True
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

    async def close(self) -> None:
        await self.finish()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def transcripts(self) -> AsyncIterator[dict]:
        """Yield {'text': str, 'is_final': bool} as Deepgram produces them.

        Empty transcripts are dropped — Deepgram emits them during silence and
        forwarding them would clear the caption the user is reading."""
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "Error":
                    log.warning("deepgram error: %s", str(msg)[:200])
                    continue
                if msg.get("type") != "Results":
                    continue
                alts = (msg.get("channel") or {}).get("alternatives") or []
                text = (alts[0].get("transcript") if alts else "") or ""
                if not text.strip():
                    continue
                yield {"text": text.strip(), "is_final": bool(msg.get("is_final"))}
        except Exception as e:
            log.info("deepgram stream ended: %s", e)


async def transcribe_pcm(pcm: bytes, keyterms: Optional[list] = None,
                         language: str = "en-US", model: str = "nova-3") -> str:
    """One-shot: transcribe a complete PCM buffer. Used by tests and by any
    caller that already has the whole utterance."""
    out: list = []
    async with DeepgramSession(keyterms, language, model) as s:
        async def pump():
            # Feed in realistic chunks; one giant send is not how the live
            # endpoint is meant to be driven.
            step = SAMPLE_RATE * 2 // 10          # 100ms of audio
            for i in range(0, len(pcm), step):
                await s.feed(pcm[i:i + step])
                await asyncio.sleep(0.005)
            await s.finish()      # NOT close: the final transcript follows this
        task = asyncio.create_task(pump())
        async for t in s.transcripts():
            if t["is_final"]:
                out.append(t["text"])
        await task
    return " ".join(out).strip()
