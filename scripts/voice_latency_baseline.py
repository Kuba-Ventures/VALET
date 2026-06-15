"""Headless voice-turn latency baseline (perceived-latency work, PR 0).

Measures the two dominant *server-side* segments of a conversational turn
without needing a mic, the browser, or the on-device app:

    t1 -> t2   request_sent  -> first_token   (Haiku model call)
    t2 -> t3   first_token   -> first_audio    (first-sentence TTS, real proxy)

It mirrors the production request shapes used by ``generate_response`` and
``synthesize_speech`` (same model id, the cached static-system block, and the
real proxy TTS route). It does NOT import server.py — it reconstructs the two
network calls so it stays fast and free of macOS/app init.

t0 -> t1 (local prompt assembly) and the frontend tail (chunk dispatched ->
audible playback) are not covered here — capture those in-app via the
[voice-timing] log line and the browser console line; see the PR description.

Reads ANTHROPIC_API_KEY / LICENSE_KEY / FISH_VOICE_ID from the environment or
the repo .env. Makes a handful of short, real API calls (model + TTS proxy).

Run:  ./.venv/bin/python scripts/voice_latency_baseline.py [iterations]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import anthropic
import httpx

ROOT = Path(__file__).resolve().parent.parent

# Production constants mirrored from server.py (kept in sync by hand; this is a
# diagnostic, not a runtime path).
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 250
PROXY_BASE_URL = os.getenv("PROXY_BASE_URL", "https://valetvoice.vercel.app").rstrip("/")
DEFAULT_VOICE_ID = "612b878b113047d9a770c069c8b4fdfe"

# A representative static-system block. The real one is ~6-8KB; size matters for
# prompt-cache behaviour, so we pad to a realistic length.
STATIC_SYSTEM = (
    "You are VALET, a voice-first British butler AI. Dry wit, economy of "
    "language. Reply in at most 1-2 sentences. " + ("Behavioural rules. " * 400)
)
DYNAMIC_SYSTEM = "CURRENT TIME: Monday. WEATHER: clear. SCREEN: nothing notable."
USER_TURN = "What time is it?"
TTS_SENTENCE = "It's just past nine in the evening, sir."


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def time_model_call(client: anthropic.AsyncAnthropic) -> float:
    t = time.perf_counter()
    await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {"type": "text", "text": STATIC_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": DYNAMIC_SYSTEM},
        ],
        messages=[{"role": "user", "content": USER_TURN}],
    )
    return (time.perf_counter() - t) * 1000


async def time_tts_call(http: httpx.AsyncClient, license_key: str, voice_id: str) -> float:
    url = f"{PROXY_BASE_URL}/api/proxy/tts"
    payload = {"text": TTS_SENTENCE, "reference_id": voice_id, "format": "mp3", "speed": 1.0}
    headers = {"X-License-Key": license_key, "Content-Type": "application/json"}
    t = time.perf_counter()
    r = await http.post(url, headers=headers, json=payload)
    dt = (time.perf_counter() - t) * 1000
    if r.status_code != 200:
        raise RuntimeError(f"TTS proxy returned {r.status_code}: {r.text[:200]}")
    return dt


async def main() -> int:
    _load_dotenv()
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    license_key = os.environ.get("LICENSE_KEY", "")
    voice_id = os.environ.get("FISH_VOICE_ID", "") or DEFAULT_VOICE_ID

    if not api_key:
        print("ANTHROPIC_API_KEY not set — cannot measure the model segment.")
        return 1

    client = anthropic.AsyncAnthropic(api_key=api_key)
    model_ms: list[float] = []
    tts_ms: list[float] = []

    print(f"model={MODEL}  proxy={PROXY_BASE_URL}  iterations={iterations}\n")

    for i in range(iterations):
        m = await time_model_call(client)
        model_ms.append(m)
        line = f"  iter {i+1}: model(t1->t2)={m:7.1f}ms"
        if license_key:
            async with httpx.AsyncClient(timeout=15.0) as http:
                try:
                    s = await time_tts_call(http, license_key, voice_id)
                    tts_ms.append(s)
                    line += f"   tts(t2->t3)={s:7.1f}ms"
                except Exception as e:
                    line += f"   tts ERROR: {e}"
        else:
            line += "   tts skipped (no LICENSE_KEY)"
        print(line)

    def stats(xs: list[float]) -> str:
        return f"min={min(xs):.1f} median={sorted(xs)[len(xs)//2]:.1f} max={max(xs):.1f}" if xs else "n/a"

    print(f"\nmodel  t1->t2 (ms): {stats(model_ms)}  [iter 1 is cold cache]")
    print(f"tts    t2->t3 (ms): {stats(tts_ms)}")
    if model_ms and tts_ms:
        warm_model = sorted(model_ms)[len(model_ms) // 2]
        warm_tts = sorted(tts_ms)[len(tts_ms) // 2]
        print(f"\nserver-side t1->t3 (median model + median tts): ~{warm_model + warm_tts:.0f}ms")
        print("(+ t0->t1 local assembly, + frontend network/decode tail — capture in-app)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
