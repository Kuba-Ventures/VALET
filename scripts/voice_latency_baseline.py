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
FISH_API_URL = "https://api.fish.audio/v1/tts"
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


async def time_to_first_sentence(client: anthropic.AsyncAnthropic) -> tuple[float, float]:
    """Stream a reply that tends to run 2 sentences and return
    (ms_to_first_sentence_boundary, ms_to_full_reply). The gap is the model-side
    latency PR 1 removes from first-audio: TTS can begin at the first boundary
    instead of waiting for the whole reply."""
    import re
    import time as _t
    boundary = re.compile(r"[.!?]+\s")
    prompt = "Briefly: what's the time and the weather? Two short sentences."
    t = _t.perf_counter()
    first_ms = None
    acc = ""
    async with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {"type": "text", "text": STATIC_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": DYNAMIC_SYSTEM},
        ],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for delta in stream.text_stream:
            acc += delta
            if first_ms is None and boundary.search(acc):
                first_ms = (_t.perf_counter() - t) * 1000
    full_ms = (_t.perf_counter() - t) * 1000
    return (first_ms if first_ms is not None else full_ms), full_ms


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


async def fish_tier_compare(fish_key: str, voice_id: str, iters: int) -> None:
    """PR 3 — compare Fish `latency` tiers (and a model override) directly
    against api.fish.audio, timing full-clip synthesis of one sentence. The
    `latency` win is larger in true streaming playback; this measures the
    full-clip effect (which is what the app currently sees — it buffers)."""
    url = FISH_API_URL
    headers_base = {"Authorization": f"Bearer {fish_key}", "Content-Type": "application/json"}
    variants = [
        ("normal (current)", {"latency": "normal"}, None),
        ("balanced", {"latency": "balanced"}, None),
        ("low", {"latency": "low"}, None),
        ("balanced model=s1", {"latency": "balanced"}, "s1"),
    ]
    print(f"PR 3 — Fish latency tiers (direct, full-clip), iters={iters}:\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        # warm the connection/account once (discard).
        await http.post(url, headers=headers_base,
                        json={"text": TTS_SENTENCE, "reference_id": voice_id, "format": "mp3"})
        for label, extra, model in variants:
            samples: list[float] = []
            headers = dict(headers_base)
            if model:
                headers["model"] = model
            body = {"text": TTS_SENTENCE, "reference_id": voice_id, "format": "mp3",
                    "prosody": {"speed": 1.0}, **extra}
            ok = True
            for _ in range(iters):
                t = time.perf_counter()
                r = await http.post(url, headers=headers, json=body)
                if r.status_code != 200:
                    print(f"  {label}: HTTP {r.status_code} {r.text[:120]}")
                    ok = False
                    break
                samples.append((time.perf_counter() - t) * 1000)
            if ok and samples:
                samples.sort()
                print(f"  {label:22s} median={samples[len(samples)//2]:7.1f}ms  "
                      f"(min={samples[0]:.0f} max={samples[-1]:.0f})")


async def main() -> int:
    _load_dotenv()
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    license_key = os.environ.get("LICENSE_KEY", "")
    voice_id = os.environ.get("FISH_VOICE_ID", "") or DEFAULT_VOICE_ID

    # PR 3 comparison runs without the Anthropic key (Fish-only).
    if "--fish" in sys.argv:
        fish_key = os.environ.get("FISH_API_KEY", "")
        if not fish_key:
            print("FISH_API_KEY not set — cannot measure Fish tiers directly.")
            return 1
        await fish_tier_compare(fish_key, voice_id, iterations)
        return 0

    if not api_key:
        print("ANTHROPIC_API_KEY not set — cannot measure the model segment.")
        return 1

    client = anthropic.AsyncAnthropic(api_key=api_key)

    # PR 1 comparison: streaming time-to-first-sentence vs full reply.
    if "--stream" in sys.argv:
        first_list: list[float] = []
        full_list: list[float] = []
        print("PR 1 — model time-to-first-sentence (streaming) vs full reply:\n")
        for i in range(iterations):
            first_ms, full_ms = await time_to_first_sentence(client)
            first_list.append(first_ms)
            full_list.append(full_ms)
            print(f"  iter {i+1}: first_sentence={first_ms:7.1f}ms   full_reply={full_ms:7.1f}ms"
                  f"   saved={full_ms - first_ms:7.1f}ms")
        med_first = sorted(first_list)[len(first_list) // 2]
        med_full = sorted(full_list)[len(full_list) // 2]
        print(f"\nmedian first_sentence={med_first:.0f}ms  full_reply={med_full:.0f}ms")
        print(f"model-side latency PR 1 removes from first-audio (median): ~{med_full - med_first:.0f}ms")
        return 0

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
