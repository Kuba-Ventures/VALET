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
    return { start() {}, stop() {}, pause() {}, resume() {} };
  }

  const recognition = new SR();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  let shouldListen = false;
  let paused = false;
  // Track rapid start/end cycles — if recognition keeps ending without ever
  // producing audio, something at the OS/device layer is wrong and infinite
  // restarts just burn CPU. Back off and tell the user.
  let lastStartAt = Date.now();
  let rapidEndStreak = 0;
  let backoffUntil = 0;
  // Once we've seen at least one successful audio capture, we know the mic
  // works. Subsequent rapid-ends are SR-side hiccups, not mic failures — we
  // still back off to save CPU, but no longer pop the alarming user toast.
  let everCaptured = false;
  const RAPID_END_THRESHOLD_MS = 350;
  const RAPID_END_LIMIT = 8;
  const BACKOFF_MS = 3000;

  recognition.onstart = () => {
    lastStartAt = Date.now();
    console.log("[voice] recognition started");
    setDiagRecog("started (waiting for audio)");
  };

  recognition.onaudiostart = () => {
    rapidEndStreak = 0;
    everCaptured = true;
    console.log("[voice] audio capture started");
    setDiagRecog("capturing audio");
  };

  const transcriptEl = document.getElementById("diag-transcript");
  function setDiagRecog(_t: string) { /* recog state line removed from UI */ }

  recognition.onresult = (event: any) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const text = event.results[i][0].transcript.trim();
      const isFinal = event.results[i].isFinal;
      console.log(`[voice] ${isFinal ? "FINAL" : "interim"}:`, text);
      if (transcriptEl) {
        transcriptEl.textContent = text;
      }
      // Emit BOTH interim and final. The wake controller checks the wake phrase
      // on interim (so the orb wakes the instant you say it) and only treats
      // FINAL transcripts as commands (so nothing gets clipped mid-sentence).
      if (text) onTranscript(text, isFinal);
    }
  };

  recognition.onend = () => {
    const elapsed = Date.now() - lastStartAt;
    if (elapsed < RAPID_END_THRESHOLD_MS) {
      rapidEndStreak += 1;
    } else {
      rapidEndStreak = 0;
    }
    console.log(`[voice] recognition ended (shouldListen=${shouldListen}, paused=${paused}, elapsed=${elapsed}ms, streak=${rapidEndStreak})`);

    if (rapidEndStreak >= RAPID_END_LIMIT) {
      backoffUntil = Date.now() + BACKOFF_MS;
      rapidEndStreak = 0;
      // Always backoff silently — the big ERROR badge is reserved for real
      // user-actionable problems. SR cycling is usually transient (Chrome's
      // speech service blip, brief network drop, etc.) and the mic itself is
      // proven via the getUserMedia waveform chart in the diag panel.
      console.warn(`[voice] SR rapid-end backoff (everCaptured=${everCaptured})`);
      setDiagRecog(everCaptured ? "recognizer reconnecting…" : "recognizer can't reach service");
    } else {
      setDiagRecog(`ended after ${elapsed}ms (streak=${rapidEndStreak})`);
    }

    if (shouldListen && !paused) {
      const wait = Math.max(0, backoffUntil - Date.now());
      setTimeout(() => {
        if (!shouldListen || paused) return;
        try {
          recognition.start();
        } catch (e) {
          console.warn("[voice] restart failed:", e);
        }
      }, wait);
    }
  };

  recognition.onerror = (event: any) => {
    console.warn("[voice] recognition error:", event.error, event.message || "");
    if (event.error === "not-allowed") {
      onError("Microphone access denied. Please allow microphone access.");
      shouldListen = false;
    } else if (event.error === "no-speech") {
      // Normal, just restart
    } else if (event.error === "aborted") {
      // Expected during pause
    }
  };

  return {
    start() {
      console.log("[voice] start() called");
      shouldListen = true;
      paused = false;
      try {
        recognition.start();
      } catch (e) {
        console.warn("[voice] start() threw:", e);
      }
    },
    stop() {
      console.log("[voice] stop() called");
      shouldListen = false;
      paused = false;
      recognition.stop();
    },
    pause() {
      console.log("[voice] pause() called");
      paused = true;
      recognition.stop();
    },
    resume() {
      console.log("[voice] resume() called (shouldListen=" + shouldListen + ")");
      paused = false;
      if (shouldListen) {
        try {
          recognition.start();
        } catch (e) {
          console.warn("[voice] resume() start threw:", e);
        }
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
      // Resume audio context (browser autoplay policy)
      if (audioCtx.state === "suspended") {
        await audioCtx.resume();
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
