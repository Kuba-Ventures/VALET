/**
 * Deepgram microphone capture — push-to-talk only.
 *
 * WebKit's built-in recognizer is fixed at en-US and cannot be told which words
 * the user actually says; that is how "GitHub" came back as "ghetto". Deepgram
 * is materially better on accented English, which is the point for a
 * French-speaking user.
 *
 * DELIBERATELY PUSH-TO-TALK ONLY. Always-listening keeps the built-in
 * recognizer. Streaming a permanently-open connection would bill by the minute
 * for every idle hour and would need per-user gating to be safe — so the meter
 * only runs while ⌃⌥ is physically held.
 *
 * Audio contract (must match `deepgram_stt.py`): 16 kHz, mono, signed 16-bit
 * little-endian PCM. Anything else transcribes as gibberish rather than failing
 * loudly, so the resampling below is the fragile part worth reading twice.
 *
 * Every failure path resolves to empty rather than throwing: the caller then
 * falls back to whatever the built-in recognizer heard. A degraded transcript
 * beats a dead microphone.
 */

const TARGET_RATE = 16000;
// The relay stays open for a beat after the key is released so Deepgram's final
// transcript can land. Past this we take whatever we have.
const FINAL_GRACE_MS = 1500;

type Ready = { ok: true } | { ok: false; reason: string };

export interface DeepgramMic {
  /** True when the backend advertised a working Deepgram key. */
  available(): boolean;
  /** Begin capturing. Safe to call twice; the second call is ignored. */
  start(): Promise<Ready>;
  /** Stop capturing and resolve with the final transcript ("" if none). */
  stop(): Promise<string>;
  /** Abort without dispatching — the hold turned out to be a shortcut. */
  cancel(): void;
  /** Interim transcripts, for the live caption. */
  onInterim(cb: (text: string) => void): void;
}

function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

/**
 * Downsample a Float32 block to 16 kHz Int16.
 *
 * Browsers give us the hardware rate (typically 44.1/48 kHz) and Deepgram is
 * told to expect exactly 16 kHz, so this conversion is not optional. Averaging
 * across each source window rather than point-sampling avoids the aliasing that
 * makes consonants mushy — which matters most for exactly the accented speech
 * this exists to transcribe.
 */
export function downsampleToInt16(input: Float32Array, inputRate: number): Int16Array {
  if (inputRate === TARGET_RATE) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  const ratio = inputRate / TARGET_RATE;
  const outLength = Math.floor(input.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    const avg = end > start ? sum / (end - start) : 0;
    const s = Math.max(-1, Math.min(1, avg));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function createDeepgramMic(): DeepgramMic {
  let enabled = false;
  let ws: WebSocket | null = null;
  let stream: MediaStream | null = null;
  let ctx: AudioContext | null = null;
  let node: ScriptProcessorNode | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let finals: string[] = [];
  let interimCb: ((t: string) => void) | null = null;
  let capturing = false;
  let discarding = false;

  // The backend advertises whether a key is configured; without one we never
  // open a socket and the caller stays on the built-in recognizer.
  fetch("/api/config")
    .then((r) => r.json())
    .then((c) => { enabled = c?.stt === "deepgram"; })
    .catch(() => { enabled = false; });

  function teardownAudio() {
    try { node?.disconnect(); } catch { /* already gone */ }
    try { source?.disconnect(); } catch { /* already gone */ }
    // Release the mic so the OS indicator goes out between turns — leaving it
    // held is what makes a laptop feel like it's always listening.
    try { stream?.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
    try { ctx?.close(); } catch { /* ignore */ }
    node = null; source = null; stream = null; ctx = null;
  }

  async function start(): Promise<Ready> {
    if (!enabled) return { ok: false, reason: "not_configured" };
    if (capturing) return { ok: true };
    capturing = true;
    discarding = false;
    finals = [];
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      ctx = new AudioContext();
      source = ctx.createMediaStreamSource(stream);
      // ScriptProcessorNode is deprecated in favour of AudioWorklet, but it is
      // reliable inside WKWebView (where VALET actually runs) and needs no
      // separate module file in the bundle. The deprecation is cosmetic here.
      node = ctx.createScriptProcessor(4096, 1, 1);

      const socket = new WebSocket(wsUrl("/ws/stt"));
      socket.binaryType = "arraybuffer";
      ws = socket;

      const opened = await new Promise<boolean>((resolve) => {
        const t = setTimeout(() => resolve(false), 3000);
        socket.onopen = () => { clearTimeout(t); resolve(true); };
        socket.onerror = () => { clearTimeout(t); resolve(false); };
      });
      if (!opened) throw new Error("stt socket did not open");

      socket.onmessage = (ev) => {
        let msg: any;
        try { msg = JSON.parse(ev.data as string); } catch { return; }
        if (msg?.type === "stt_unavailable") {
          enabled = false;                 // stop trying for the rest of the session
          return;
        }
        if (msg?.type !== "stt_transcript" || discarding) return;
        if (msg.isFinal) finals.push(String(msg.text || ""));
        else if (interimCb) interimCb(String(msg.text || ""));
      };

      node.onaudioprocess = (e) => {
        if (!capturing || socket.readyState !== WebSocket.OPEN) return;
        const pcm = downsampleToInt16(e.inputBuffer.getChannelData(0), ctx!.sampleRate);
        socket.send(pcm.buffer);
      };
      source.connect(node);
      // Route to a muted gain node rather than the speakers: a ScriptProcessor
      // only runs while connected to a destination, but connecting it directly
      // would play the microphone back through the speakers.
      const sink = ctx.createGain();
      sink.gain.value = 0;
      node.connect(sink);
      sink.connect(ctx.destination);
      return { ok: true };
    } catch (err) {
      capturing = false;
      teardownAudio();
      try { ws?.close(); } catch { /* ignore */ }
      ws = null;
      return { ok: false, reason: String(err) };
    }
  }

  async function stop(): Promise<string> {
    if (!capturing) return "";
    capturing = false;
    const socket = ws;
    teardownAudio();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      ws = null;
      return finals.join(" ").trim();
    }
    // Tell the relay we're done, then wait briefly: Deepgram's final transcript
    // arrives AFTER the close signal, not before it.
    try { socket.send("stop"); } catch { /* ignore */ }
    const before = finals.length;
    await new Promise<void>((resolve) => {
      const done = () => resolve();
      const t = setTimeout(done, FINAL_GRACE_MS);
      const poll = setInterval(() => {
        if (finals.length > before) { clearTimeout(t); clearInterval(poll); done(); }
      }, 60);
      socket.addEventListener("close", () => {
        clearTimeout(t); clearInterval(poll); done();
      });
    });
    try { socket.close(); } catch { /* ignore */ }
    ws = null;
    return finals.join(" ").replace(/\s+/g, " ").trim();
  }

  function cancel() {
    discarding = true;
    capturing = false;
    finals = [];
    teardownAudio();
    try { ws?.close(); } catch { /* ignore */ }
    ws = null;
  }

  return {
    available: () => enabled,
    start,
    stop,
    cancel,
    onInterim: (cb) => { interimCb = cb; },
  };
}
