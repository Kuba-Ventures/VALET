# STT provider investigation — Web Speech vs streaming (Deepgram / AssemblyAI)

> Perceived-latency track, investigation deliverable (no code). Companion to PRs 0–4.
> Date: 2026-06-15.

## TL;DR / recommendation

**Do not switch off Web Speech yet — but close the measurement gap first, then decide on data.**

- Web Speech is **free, zero-dependency, zero per-minute cost**, and already shipping. Its real costs are (a) **uncontrollable end-of-speech / finalization latency** and (b) **accuracy** — the entire mishearing apparatus in `wakeWord.ts` and the reconnect/backoff machinery in `voice.ts` are symptoms.
- **PR 0 does not actually measure STT finalization latency.** `t0` is *defined as* the STT-final transcript, so the latency that matters here (you-stop-talking → final emitted) is **upstream of `t0` and uncaptured**. Before paying for a provider, add the ~5-line instrumentation in §3 and capture real numbers **on the Tauri WebView (prod), not just Chrome (dev)** — the two use different speech engines.
- **PR 2 (push-to-talk) already removes the worst of this for power users** at zero cost: holding the key skips wake-word matching and dispatches on release, sidestepping both the mishearing-on-wake class and the finalization pause.
- **If** measured finalization latency is consistently high (≳500 ms) or accuracy is hurting UX after PRs 1/2/4 land, **then** pilot **AssemblyAI Universal-Streaming** (cheapest at ~$0.15/hr, ~300 ms latency, immutable transcripts, tunable endpointing) behind the existing proxy/license pattern, flag-gated, and measure the delta against this baseline. It adds a proxy route, per-minute cost, and webview mic-audio streaming — **don't do it on assumption.**

---

## 1. What we run today

`frontend/src/voice.ts` wraps the browser **Web Speech API** (`webkitSpeechRecognition`, `continuous = true`, `interimResults = true`). `frontend/src/wakeWord.ts` gates it (passive wake-word scan → active command forwarding). End-of-speech = the recognizer's `isFinal` result, which `main.ts` forwards to the backend as `{type:"transcript", isFinal:true}` — this is the perceived-latency clock's **`t0`**.

**Engine split (important):** in **dev** the app runs in **Chrome**, whose Web Speech implementation streams audio to **Google's** servers. In the **signed build** the frontend runs inside a **Tauri WebView** — **WebKit/WKWebView on macOS**, which uses **Apple's** on-device/served speech recognition. These are *different recognizers* with different latency, accuracy, endpointing, and failure modes. **Any STT measurement or accuracy judgment must be made in the prod WebView, not Chrome.** (This engine dependency is itself a reliability argument — see §4.)

---

## 2. Cost & dependency comparison

| | **Web Speech (today)** | **AssemblyAI Universal-Streaming** | **Deepgram (Flux / Nova-3 streaming)** |
|---|---|---|---|
| Per-minute cost | **$0** | ~$0.15/hr ≈ **$0.0025/min** (Universal-Streaming); Universal-3 Pro ~$0.45/hr | ~$0.0077/min PAYG ≈ **$0.46/hr** streaming (Flux for low-latency endpointing) |
| Billing basis | — | **total session duration** (open stream, not just speech) | streamed audio minutes |
| Median latency | unmeasured (opaque) | **~300 ms**, partials + final | **<1 s**, partials; Flux tuned for fast end-of-speech |
| Endpointing control | **none** (recognizer decides) | **intelligent + tunable** turn detection | **configurable** endpointing/utterance-end |
| Transcript stability | interim may rewrite | **immutable** (emitted words don't change) | interim may rewrite (configurable) |
| Dependency | OS WebView speech engine (Apple in prod, Google in dev) — **VALET can't version-pin or tune** | Anthropic-style: our proxy + their API | our proxy + their API |
| Network | required (cloud recognizer) | required | required |
| Integration cost | **already done** | proxy route + **stream mic PCM from webview over WS** + per-user metering | same |
| Privacy/metering | none of ours | runs through our proxy (like TTS/LLM) | same |

Sources: [AssemblyAI pricing](https://www.assemblyai.com/pricing) · [AssemblyAI Universal-Streaming](https://www.assemblyai.com/universal-streaming) · [Deepgram pricing 2026](https://diyai.io/ai-tools/speech-to-text/deepgram-pricing-2026/) · [STT API comparison 2026](https://futureagi.com/blog/speech-to-text-apis-in-2026-benchmarks-pricing-developer-s-decision-guide/) · [STT API pricing June 2026](https://www.buildmvpfast.com/api-costs/transcription).

**Rough monthly envelope (per user, AssemblyAI @ $0.0025/min):** ~30 min of *actual captured audio*/day → ~$2.25/mo. **Caveat:** AssemblyAI bills **total session duration**, so an always-open mic stream bills 24/7, not just while speaking — you'd open the stream **only during active capture** (e.g. while push-to-talk is held, or a short window after wake) to keep cost near the "actual audio" figure. Deepgram streaming is ~3× the per-minute rate.

---

## 3. Latency — what PR 0 captures, and the gap

PR 0's marks start at **`t0` = STT-final**:

```
t0 speech_final → t1 request_sent → t2 first_token → t3 first_audio
```

**Finalization latency** (you stop speaking → recognizer emits the final) is **before `t0`** and **not instrumented**. So the headline number this investigation needs is not currently produced. To capture it, add an `onspeechend` timestamp in `voice.ts` and diff it against the next final `onresult` (≈5 lines, no provider change):

```ts
// in createVoiceInput, alongside the existing handlers:
let speechEndedAt = 0;
recognition.onspeechend = () => { speechEndedAt = performance.now(); };
// inside onresult, when results[i].isFinal:
if (speechEndedAt) {
  console.log(`[stt-timing] speechend→final: ${Math.round(performance.now() - speechEndedAt)}ms`);
  speechEndedAt = 0;
}
```

Run that in the **prod WebView** across ~20 turns to get a real median before deciding. Expectation from Web Speech's architecture: finalization waits on a silence/endpoint timer plus a server round-trip, typically **several hundred ms to >1 s**, and **not adjustable**. Dedicated streaming providers advertise **~300 ms** with **tunable** endpointing — that delta is the prize, but it must be measured, not assumed.

(The existing PR-0 browser line, `[voice-timing] speech_final→first_audio_played`, measures everything *after* `t0` and is unaffected by STT choice — useful as the denominator when weighing whether STT latency is worth paying down.)

---

## 4. Reliability — the evidence is already in the code

Web Speech's reliability problems are visible as the workarounds built around it:

- **Accuracy / mishearings** — `wakeWord.ts` carries an entire mishearing vocabulary because the recognizer rarely transcribes the one-syllable wake word cleanly: `VEE_WAKE_VARIANTS` (`v, b, c, d, e, g, p, t, z, bee, fee`, lines 71–75) and `VEE_FULL_VARIANTS = ["av"]` — the recognizer **collapses "hey vee" into "AV"** (lines 80–85). This is direct evidence the recognizer's output is noisy enough to need defensive pattern-matching. A provider with **custom vocabulary / keyword boosting** would target this directly.
- **Connection instability** — `voice.ts` has rapid-end backoff: `RAPID_END_THRESHOLD_MS=350`, `RAPID_END_LIMIT=8`, `BACKOFF_MS=3000`, an `everCaptured` flag, and a "recognizer can't reach service" state (lines 41–115). The recognizer ends without producing audio and must be restarted — a cloud/engine dependency, not a mic problem (the mic is independently proven via the getUserMedia waveform).
- **No control surface** — no endpointing sensitivity, no immutable-transcript guarantee (interims can rewrite), no SLA, and historically a ~60 s continuous-session cap that forces restarts. Behavior is at the mercy of the embedded WebView's speech engine, which **changes with the OS** and differs dev↔prod (§1).

Streaming providers remove the engine-version dependency and add endpointing/vocabulary control and immutable transcripts — but introduce a per-minute cost and a new external dependency (mitigated by routing through the existing license proxy, as LLM/TTS already do).

---

## 5. What a switch actually costs (scope, if pursued)

1. **Webview mic capture → PCM stream.** Today Web Speech owns the mic. A provider needs raw audio: `getUserMedia` → `AudioWorklet`/`MediaRecorder` → 16 kHz PCM frames over a WebSocket. The waveform code in `main.ts` already proves mic capture works; this adds the streaming encoder.
2. **A proxy route** (`/api/proxy/stt` in `product-site`) mirroring the Fish/Anthropic pattern: inject the vendor key, meter per license, enforce fair-use. (Same shape as `lib/proxy/fish.ts`.)
3. **Per-minute metering + fair-use** for a *time-based* (not token-based) product — new pricing surface.
4. **Open-stream discipline** — stream only during active capture (push-to-talk held, or a bounded window after wake) so session-duration billing stays near actual-audio cost.
5. **Keep Web Speech as the free fallback** (no license / offline / provider down), behind a flag — don't hard-cut.

This is a meaningful chunk of work and an ongoing cost. It is justified **only** if §3's measured finalization latency and §4's accuracy are demonstrably hurting the experience after the zero-cost wins (PRs 1/2/4) land.

---

## 6. Decision gate

Switch **only if all** of these hold after PRs 0–4 are live and the §3 instrumentation has run on the prod WebView:

1. Measured `speechend→final` median is **≳500 ms** (i.e. STT finalization is a material slice of perceived latency), **and**
2. Mishearing/accuracy is still hurting UX **despite** push-to-talk (PR 2) and the wake-word mitigations, **and**
3. The per-user monthly cost (open-stream-during-capture only) is acceptable for the business.

If so, **pilot AssemblyAI Universal-Streaming** (lowest cost + latency, immutable transcripts) behind a flag and the proxy, measuring the delta against the PR-0/§3 baseline before committing. Otherwise, **stay on Web Speech** and revisit if the OS WebView's recognizer regresses.
