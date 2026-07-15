/**
 * Voice input (Web Speech API) and audio output (AudioContext) for VALET.
 */

// ---------------------------------------------------------------------------
// Speech Recognition
// ---------------------------------------------------------------------------

export interface VoiceInput {
  start(): void;
  stop(): void;
  pause(): void;
  resume(): void;
  /** Force the recognizer to emit a FINAL for buffered audio WITHOUT ending the
   *  listen loop (onend restarts it). Used by push-to-talk on key release. */
  finalize(): void;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const webkitSpeechRecognition: any;

export function createVoiceInput(
  onTranscript: (text: string, isFinal: boolean) => void,
  onError: (msg: string) => void
): VoiceInput {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const SR = (window as any).SpeechRecognition || (typeof webkitSpeechRecognition !== "undefined" ? webkitSpeechRecognition : null);
  if (!SR) {
    onError("Speech recognition not supported in this browser");
    return { start() {}, stop() {}, pause() {}, resume() {}, finalize() {} };
  }

  let shouldListen = false;
  let paused = false;
  // Track rapid start/end cycles — if recognition keeps ending without ever
  // producing audio, something at the OS/device layer is wrong and infinite
  // restarts just burn CPU (and, worse, machine-gun the macOS mic indicator).
  // Back off with escalating delay and recreate the recognizer.
  let lastStartAt = Date.now();
  let rapidEndStreak = 0;
  let backoffUntil = 0;
  // Once we've seen at least one successful audio capture, we know the mic
  // works. Subsequent rapid-ends are SR-side hiccups, not mic failures — we
  // still back off to save CPU, but no longer pop the alarming user toast.
  let everCaptured = false;
  const RAPID_END_THRESHOLD_MS = 350;
  // Back off sooner than before (was 8) so we emit only a couple of mic
  // acquire/release blips before pausing, instead of a long audible burst.
  const RAPID_END_LIMIT = 4;
  const BASE_BACKOFF_MS = 3000;
  const MAX_BACKOFF_MS = 20000;
  // Space out restarts while we've never managed to capture audio, so a mic we
  // can't yet acquire (e.g. macOS hasn't released the just-quit process's
  // Speech session on a quick relaunch) isn't hammered — that hammering is what
  // toggles the mic indicator on/off audibly.
  const RESTART_SPACING_MS = 400;
  // Current backoff, escalated on each consecutive failed round and reset the
  // moment audio is actually captured.
  let backoffMs = BASE_BACKOFF_MS;

  const transcriptEl = document.getElementById("diag-transcript");
  function setDiagRecog(_t: string) { /* recog state line removed from UI */ }

  // WebKit's SpeechRecognition (Tauri/WKWebView on macOS) tends to stay wedged
  // once it fails to acquire a session — restarting the SAME object just keeps
  // failing. So we hold the instance in a mutable `recognition` and REBUILD it
  // on backoff. Handlers close over their own `rec` and no-op if a newer
  // instance has since replaced it, so a stale object firing a late `onend`
  // can't drive a second restart.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let recognition: any = null;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function buildRecognition(): any {
    const rec = new SR();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";

    rec.onstart = () => {
      if (rec !== recognition) return;
      lastStartAt = Date.now();
      console.log("[voice] recognition started");
      setDiagRecog("started (waiting for audio)");
    };

    rec.onaudiostart = () => {
      if (rec !== recognition) return;
      rapidEndStreak = 0;
      everCaptured = true;
      backoffMs = BASE_BACKOFF_MS; // recovered — reset the escalation
      console.log("[voice] audio capture started");
      setDiagRecog("capturing audio");
    };

    rec.onresult = (event: any) => {
      if (rec !== recognition) return;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text = event.results[i][0].transcript.trim();
        const isFinal = event.results[i].isFinal;
        console.log(`[voice] ${isFinal ? "FINAL" : "interim"}:`, text);
        if (transcriptEl) {
          transcriptEl.textContent = text;
        }
        // Emit BOTH interim and final. The wake controller checks the wake
        // phrase on interim (so the orb wakes the instant you say it) and only
        // treats FINAL transcripts as commands (so nothing gets clipped).
        if (text) onTranscript(text, isFinal);
      }
    };

    rec.onend = () => {
      if (rec !== recognition) return; // superseded by a rebuilt instance
      const elapsed = Date.now() - lastStartAt;
      if (elapsed < RAPID_END_THRESHOLD_MS) {
        rapidEndStreak += 1;
      } else {
        rapidEndStreak = 0;
      }
      console.log(`[voice] recognition ended (shouldListen=${shouldListen}, paused=${paused}, elapsed=${elapsed}ms, streak=${rapidEndStreak})`);

      let recreate = false;
      if (rapidEndStreak >= RAPID_END_LIMIT) {
        backoffUntil = Date.now() + backoffMs;
        rapidEndStreak = 0;
        // A stuck WebKit recognizer won't recover in place — swap in a fresh
        // instance so it gets a clean session once the OS frees the mic.
        recreate = true;
        // Always backoff silently — the big ERROR badge is reserved for real
        // user-actionable problems. SR cycling is usually transient (a speech
        // service blip, brief network drop, or the OS still releasing the mic
        // from a just-quit instance) and the mic itself is proven via the
        // getUserMedia waveform chart in the diag panel.
        console.warn(`[voice] SR rapid-end backoff ${backoffMs}ms (everCaptured=${everCaptured})`);
        setDiagRecog(everCaptured ? "recognizer reconnecting…" : "recognizer can't reach service");
        // Escalate the NEXT round's wait so we stop churning if this persists.
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
      } else {
        setDiagRecog(`ended after ${elapsed}ms (streak=${rapidEndStreak})`);
      }

      if (shouldListen && !paused) {
        if (recreate) recreateRecognition();
        let wait = Math.max(0, backoffUntil - Date.now());
        // Between the first few rapid ends (before backoff kicks in) space
        // attempts out while we've never captured, so the mic indicator isn't
        // toggled on/off in a tight burst.
        if (wait === 0 && rapidEndStreak > 0 && !everCaptured) {
          wait = RESTART_SPACING_MS;
        }
        setTimeout(() => {
          if (!shouldListen || paused) return;
          startRecognition();
        }, wait);
      }
    };

    rec.onerror = (event: any) => {
      if (rec !== recognition) return;
      console.warn("[voice] recognition error:", event.error, event.message || "");
      if (event.error === "not-allowed") {
        onError("Microphone access denied. Please allow microphone access.");
        shouldListen = false;
      } else if (event.error === "no-speech") {
        // Normal, just restart
      } else if (event.error === "aborted") {
        // Expected during pause / recreate
      }
    };

    return rec;
  }

  // Start the CURRENT recognizer instance; swallow the "already started" throw.
  function startRecognition() {
    try {
      recognition.start();
    } catch (e) {
      console.warn("[voice] start failed:", e);
    }
  }

  // Replace the live recognizer with a fresh instance. The old one is aborted
  // (its late onend is ignored via the `rec !== recognition` guard).
  function recreateRecognition() {
    const old = recognition;
    recognition = buildRecognition();
    if (old) {
      try {
        if (typeof old.abort === "function") old.abort();
        else old.stop();
      } catch {
        // already stopped
      }
    }
  }

  recognition = buildRecognition();

  return {
    start() {
      console.log("[voice] start() called");
      shouldListen = true;
      paused = false;
      startRecognition();
    },
    stop() {
      console.log("[voice] stop() called");
      shouldListen = false;
      paused = false;
      try {
        recognition.stop();
      } catch (e) {
        console.warn("[voice] stop() threw:", e);
      }
    },
    pause() {
      console.log("[voice] pause() called");
      paused = true;
      try {
        recognition.stop();
      } catch (e) {
        console.warn("[voice] pause() threw:", e);
      }
    },
    resume() {
      console.log("[voice] resume() called (shouldListen=" + shouldListen + ")");
      paused = false;
      if (shouldListen) startRecognition();
    },
    finalize() {
      // Flush buffered audio as a FINAL result. shouldListen/paused are left
      // untouched, so onend restarts the recognizer and listening continues.
      console.log("[voice] finalize() called");
      try {
        recognition.stop();
      } catch (e) {
        console.warn("[voice] finalize() stop threw:", e);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Audio Player
// ---------------------------------------------------------------------------

export interface AudioPlayer {
  enqueue(base64: string): Promise<void>;
  stop(): void;
  getAnalyser(): AnalyserNode;
  onFinished(cb: () => void): void;
  /** Mark end-of-speech for the next turn; the first chunk that actually
   *  plays logs the speech→first-audible latency (perceived-latency baseline). */
  markTurnStart(): void;
}

export function createAudioPlayer(): AudioPlayer {
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.8;
  analyser.connect(audioCtx.destination);

  const queue: AudioBuffer[] = [];
  let isPlaying = false;
  let currentSource: AudioBufferSourceNode | null = null;
  let finishedCallback: (() => void) | null = null;
  // Perceived-latency instrumentation: timestamp of the last end-of-speech,
  // consumed (and cleared) by the first chunk that actually plays this turn.
  let turnStartAt: number | null = null;

  function playNext() {
    if (queue.length === 0) {
      isPlaying = false;
      currentSource = null;
      finishedCallback?.();
      return;
    }

    isPlaying = true;
    const buffer = queue.shift()!;
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);
    currentSource = source;

    source.onended = () => {
      if (currentSource === source) {
        playNext();
      }
    };

    if (turnStartAt !== null) {
      const ms = Math.round(performance.now() - turnStartAt);
      console.log(`[voice-timing] speech_final→first_audio_played: ${ms}ms`);
      turnStartAt = null;
    }
    source.start();
  }

  return {
    async enqueue(base64: string) {
      // Resume audio context (browser autoplay policy). WKWebView (Tauri on
      // macOS) parks the context in "interrupted" — not "suspended" — after the
      // window is minimized, so gate on "not running" to cover both states.
      // Without this, TTS decodes and starts but plays silently on restore.
      if (audioCtx.state !== "running") {
        try {
          await audioCtx.resume();
        } catch (err) {
          console.warn("[audio] resume failed:", err);
        }
      }

      try {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        const audioBuffer = await audioCtx.decodeAudioData(bytes.buffer.slice(0));
        queue.push(audioBuffer);
        if (!isPlaying) playNext();
      } catch (err) {
        console.error("[audio] decode error:", err);
        // Skip bad audio, continue
        if (!isPlaying && queue.length > 0) playNext();
      }
    },

    stop() {
      queue.length = 0;
      if (currentSource) {
        try {
          currentSource.stop();
        } catch {
          // Already stopped
        }
        currentSource = null;
      }
      isPlaying = false;
    },

    getAnalyser() {
      return analyser;
    },

    onFinished(cb: () => void) {
      finishedCallback = cb;
    },

    markTurnStart() {
      turnStartAt = performance.now();
    },
  };
}
