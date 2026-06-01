/**
 * JARVIS — Main entry point.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 */

import { createOrb, type OrbState } from "./orb";
import { createAudioPlayer } from "./voice";
import { createWakeWord } from "./wakeWord";
import { createSocket } from "./ws";
import { openSettings, checkFirstTimeSetup } from "./settings";
import { createProcessPanel, type ProcessEvent } from "./processPanel";
import { createDesignPanel, type DesignEvent } from "./designPanel";
import "./style.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type State = "idle" | "listening" | "thinking" | "speaking";
let currentState: State = "idle";

// Wake-word listening toggle. Persisted across reloads via localStorage so the
// user's preference survives a refresh. Controls only the frontend's wake-word
// listening — the backend service is unaffected.
const WAKE_STATE_KEY = "jarvis.wakeListening";
let isSleeping = localStorage.getItem(WAKE_STATE_KEY) === "sleeping";

const statusEl = document.getElementById("status-text")!;
const errorEl = document.getElementById("error-text")!;
const replyEl = document.getElementById("jarvis-reply")!;

function showReply(text: string) {
  const trimmed = (text || "").trim();
  if (!trimmed) return;
  replyEl.textContent = trimmed;
  replyEl.classList.add("visible");
}
replyEl.addEventListener("click", () => {
  replyEl.classList.remove("visible");
});

let _errorHideTimer: number | undefined;
function showError(msg: string) {
  errorEl.textContent = msg;
  errorEl.classList.add("visible");
  if (_errorHideTimer) window.clearTimeout(_errorHideTimer);
  _errorHideTimer = window.setTimeout(() => {
    errorEl.classList.remove("visible");
  }, 5000);
}
function hideError() {
  if (_errorHideTimer) window.clearTimeout(_errorHideTimer);
  errorEl.classList.remove("visible");
}

function updateStatus(state: State) {
  if (isSleeping) {
    statusEl.textContent = "";
    return;
  }
  const labels: Record<State, string> = {
    idle: "",
    listening: "listening...",
    thinking: "thinking...",
    speaking: "",
  };
  statusEl.textContent = labels[state];
}

// ---------------------------------------------------------------------------
// Init components
// ---------------------------------------------------------------------------

const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;
const orb = createOrb(canvas);

const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = `${wsProto}//${window.location.host}/ws/voice`;
const socket = createSocket(WS_URL);

const audioPlayer = createAudioPlayer();
orb.setAnalyser(audioPlayer.getAnalyser());

// Live "what JARVIS is doing" panel. Hidden until the first event arrives.
const processPanel = createProcessPanel();
const designPanel = createDesignPanel();

// Ship/Scrap button handlers → synthesize a fake transcript so the existing
// fast-action path runs (single source of truth for the ship/scrap pipeline).
designPanel.onShipClick(() => {
  socket.send({ type: "transcript", text: "ship it", isFinal: true });
});
designPanel.onScrapClick(() => {
  socket.send({ type: "transcript", text: "scrap this", isFinal: true });
});
designPanel.onMergeClick(() => {
  socket.send({ type: "transcript", text: "merge it", isFinal: true });
});
designPanel.onTargetSelect((path) => {
  socket.send({ type: "set_design_target", path });
});
designPanel.onAgentSelect((agent) => {
  socket.send({ type: "set_design_agent", agent });
});
designPanel.onNewProjectSubmit((name, baseDir) => {
  socket.send({ type: "set_design_new_project", name, base_dir: baseDir });
});

// Reflect WS connection health in the central ERROR badge.
socket.onConnectionChange((isConnected) => {
  if (isConnected) {
    hideError();
  } else {
    showError("Backend not connected. Reconnecting…");
  }
});

function transition(newState: State) {
  if (newState === currentState) return;
  currentState = newState;
  orb.setState(newState as OrbState);
  updateStatus(newState);

  switch (newState) {
    case "idle":
      if (!isSleeping) wake.resume();
      break;
    case "listening":
      if (!isSleeping) wake.resume();
      break;
    case "thinking":
      wake.pause();
      break;
    case "speaking":
      wake.pause();
      break;
  }
}

// ---------------------------------------------------------------------------
// Wake-word-gated voice input
// ---------------------------------------------------------------------------

const wake = createWakeWord(
  "jarvis", // overwritten once /api/config resolves below
  {
    onWake: () => {
      transition("listening");
    },
    onCommand: (text: string) => {
      audioPlayer.stop();
      const sent = socket.send({ type: "transcript", text, isFinal: true });
      if (sent) {
        transition("thinking");
      } else {
        showError("Backend not connected. Reconnecting…");
        transition("idle");
      }
    },
    onError: (msg: string) => {
      showError(msg);
    },
  }
);

const jarvisLabelEl = document.getElementById("jarvis-label")!;

function applyAssistantName(name: string) {
  wake.setName(name);
  jarvisLabelEl.textContent = name.toUpperCase();
}

// Default visible label matches the default wake name; updated once /api/config resolves.
applyAssistantName("jarvis");

// Pull the configured name from the backend; if the fetch fails we keep "jarvis".
fetch("/api/config")
  .then((r) => r.json())
  .then((cfg: { assistant_name?: string }) => {
    if (cfg && cfg.assistant_name) {
      applyAssistantName(cfg.assistant_name);
    }
  })
  .catch(() => { /* keep default */ });

// ---------------------------------------------------------------------------
// Audio playback finished
// ---------------------------------------------------------------------------

audioPlayer.onFinished(() => {
  // In active conversation, stay visually in "listening" between turns so the
  // user can tell the assistant is still hot. Drops back to "idle" only when
  // the wake module has been put back to passive (e.g. via Sleeping toggle).
  transition(wake.isActive() ? "listening" : "idle");
  // Audio finishing means JARVIS just wrapped a reply — arm the quiet-timer.
  // Trailing background events will reset (not kill) it; see
  // refreshPanelAutoClose. Force-closes once the stream goes quiet.
  refreshPanelAutoClose();
});

// ---------------------------------------------------------------------------
// Panel auto-close on idle
// ---------------------------------------------------------------------------
// User asked: when JARVIS finishes a turn, don't leave the process / design
// panels lingering on screen. Trigger on either (a) audio playback finishing
// or (b) the server sending status=idle. Cancel on any new activity.
const IDLE_AUTO_CLOSE_MS = 1800;
let _idleCloseTimer: number | undefined;
function scheduleIdleAutoClose() {
  cancelIdleAutoClose();
  _idleCloseTimer = window.setTimeout(() => {
    processPanel.tryAutoClose();
    designPanel.tryAutoCloseIfIdle();
  }, IDLE_AUTO_CLOSE_MS);
}
function cancelIdleAutoClose() {
  if (_idleCloseTimer !== undefined) {
    window.clearTimeout(_idleCloseTimer);
    _idleCloseTimer = undefined;
  }
}

// ---------------------------------------------------------------------------
// WebSocket messages
// ---------------------------------------------------------------------------

socket.onMessage((msg) => {
  const type = msg.type as string;

  // Idle-close is (re)evaluated at the end of this handler via
  // refreshPanelAutoClose(), once any state transition for this message has
  // been applied. We deliberately do NOT blanket-cancel here: trailing
  // background events (warm-context load, draft+browse, dispatch) must RESET
  // the quiet timer, not permanently kill it — otherwise the panel wedges
  // open after the work goes quiet. See refreshPanelAutoClose below.

  if (type === "audio") {
    const audioData = msg.data as string;
    console.log("[audio] received", audioData ? `${audioData.length} chars` : "EMPTY", "state:", currentState);
    if (audioData) {
      if (currentState !== "speaking") {
        transition("speaking");
      }
      audioPlayer.enqueue(audioData);
    } else {
      // TTS failed — no audio but still need to return to idle
      console.warn("[audio] no data received, returning to idle");
      transition("idle");
    }
    // Show reply as a persistent caption (TTS audio is ephemeral; the text is not).
    if (msg.text) {
      console.log("[JARVIS]", msg.text);
      showReply(msg.text as string);
    }
  } else if (type === "status") {
    const state = msg.state as string;
    // Panel auto-close is handled centrally by refreshPanelAutoClose() at the
    // end of this handler, keyed off the resulting currentState.
    if (state === "thinking" && currentState !== "thinking") {
      transition("thinking");
    } else if (state === "working") {
      // Task spawned — show thinking with a different label
      transition("thinking");
      statusEl.textContent = "working...";
    } else if (state === "idle") {
      transition("idle");
    }
  } else if (type === "text") {
    // Text-only path (TTS failed or skipped) — still surface it to the user.
    console.log("[JARVIS]", msg.text);
    if (msg.text) showReply(msg.text as string);
  } else if (type === "task_spawned") {
    console.log("[task]", "spawned:", msg.task_id, msg.prompt);
  } else if (type === "task_complete") {
    console.log("[task]", "complete:", msg.task_id, msg.status, msg.summary);
  } else if (type === "process_event") {
    // ProcessEventBus broadcasts — drive the live activity panel.
    const event = msg.event as ProcessEvent | undefined;
    if (event) processPanel.handleEvent(event);
  } else if (type === "close_panel") {
    // Server-side voice intent ("close it", "dismiss", etc.) closes the panel.
    processPanel.close();
  } else if (type === "design_event") {
    // Design-partner emissions — drive the Design Panel beside the orb.
    const event = msg.event as DesignEvent | undefined;
    if (event) {
      designPanel.handleEvent(event);
      // Mirror the design-session state into the Process Panel header so
      // the user can tell at a glance that voice turns are routing through
      // Opus's design partner rather than the default action router.
      if (event.type === "design.state_changed") {
        const state = (event.payload as Record<string, unknown> | undefined)?.state;
        processPanel.setDesignActive(state === "DESIGNING");
      }
    }
  } else if (type === "dictation_event") {
    // Mode 2 (chunk 21) — toggle the amber · dictation chip whenever the
    // backend transitions in/out of capturing_prompt / confirming. Any
    // state other than those two means dictation is inactive.
    const event = msg.event as { state?: string } | undefined;
    const state = event?.state ?? "idle";
    const active = state === "capturing_prompt" || state === "confirming";
    processPanel.setDictationActive(active);
  }

  // Centralised panel auto-close: re-evaluate after every message, keyed off
  // the state this message produced. While JARVIS is actively working the
  // foreground turn (thinking/speaking) the panel stays. Once the turn has
  // wrapped (idle/listening) each subsequent message — including trailing
  // background process_events — RESETS a short quiet-timer; when the stream
  // finally goes quiet the watchdog force-closes via tryAutoClose. This is
  // the safety net that survives a dropped task_done or a lingering result
  // card (both of which otherwise wedge the panel open forever).
  refreshPanelAutoClose();
});

function refreshPanelAutoClose() {
  // Keep the coarse idle-close OFF while JARVIS is actively working the
  // foreground turn (thinking/speaking) OR while the panel is tracking live
  // background tasks. In those states the panel owns its own dismissal (it
  // self-dismisses shortly after the last task_done), so a brief quiet gap
  // in a long job — e.g. a dispatched build mid-thought — never force-closes
  // it. The watchdog only arms once the turn has wrapped AND no tasks are in
  // flight, which is exactly the "lingering panel / dropped lifecycle" case
  // it exists to clean up.
  if (
    currentState === "thinking" ||
    currentState === "speaking" ||
    processPanel.hasActiveTasks()
  ) {
    cancelIdleAutoClose();
  } else {
    scheduleIdleAutoClose(); // cancels + re-arms; last quiet message wins
  }
}

// ---------------------------------------------------------------------------
// Kick off
// ---------------------------------------------------------------------------

// Start listening after a brief delay for the orb to render.
// Initial state is "idle" — passive, scanning for the wake phrase.
// reconcileWakeControl() honors the persisted Sleeping/Active preference
// so a saved "Sleeping" state doesn't briefly start the mic on page load.
setTimeout(() => {
  wake.start();
  reconcileWakeControl();
  if (!isSleeping) updateStatus("idle");
}, 1000);

// Resume AudioContext on ANY user interaction (browser autoplay policy)
function ensureAudioContext() {
  const ctx = audioPlayer.getAnalyser().context as AudioContext;
  if (ctx.state === "suspended") {
    ctx.resume().then(() => console.log("[audio] context resumed"));
  }
}
document.addEventListener("click", ensureAudioContext);
document.addEventListener("touchstart", ensureAudioContext);
document.addEventListener("keydown", ensureAudioContext, { once: true });

// Try to resume audio context on load
ensureAudioContext();

// -------- Diagnostic panel ----------------------------------------------
// Live readout of mic level + recognizer state + last transcript. Pinned to
// bottom-left; toggle visibility with Cmd+D. Useful for triaging "why isn't
// it waking" without flipping to DevTools.
const diagPanel = document.getElementById("diag-panel")!;

document.addEventListener("keydown", (e) => {
  if (e.metaKey && e.key.toLowerCase() === "d") {
    e.preventDefault();
    diagPanel.classList.toggle("hidden");
  }
});

function setDiagMic(_text: string) { /* mic device label removed from UI */ }

function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timeout: ${label} did not resolve in ${ms}ms`)), ms);
    p.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
  });
}

(async () => {
  try {
    setDiagMic("mic: enumerating devices…");
    const devices = await withTimeout(navigator.mediaDevices.enumerateDevices(), 3000, "enumerateDevices");
    const mics = devices.filter((d) => d.kind === "audioinput");
    console.log("[mic] available inputs:", mics.map((d) => `${d.label || "(no label)"} [${d.deviceId.slice(0, 8)}]`));

    setDiagMic(`mic: requesting (${mics.length} inputs found)…`);
    const stream = await withTimeout(navigator.mediaDevices.getUserMedia({ audio: true }), 5000, "getUserMedia");
    const track = stream.getAudioTracks()[0];
    console.log("[mic] bound to:", track.label, "settings:", track.getSettings());

    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 512;
    an.smoothingTimeConstant = 0.6;
    src.connect(an);
    const timeBuf = new Uint8Array(an.fftSize);
    const freqBuf = new Uint8Array(an.frequencyBinCount);
    const deviceLabel = (track.label || "unknown").slice(0, 28);
    setDiagMic(`mic: ${deviceLabel}`);

    const canvas = document.getElementById("diag-mic-canvas") as HTMLCanvasElement;
    const dpr = window.devicePixelRatio || 1;
    const W = 140;
    const H = 32;
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    const cctx = canvas.getContext("2d")!;
    cctx.scale(dpr, dpr);

    // HUD-style frequency bars + overlaid waveform line.
    function draw() {
      an.getByteFrequencyData(freqBuf);
      an.getByteTimeDomainData(timeBuf);

      cctx.clearRect(0, 0, W, H);

      // Frequency bars (cyan, behind)
      const barCount = 28;
      const binsPerBar = Math.floor(freqBuf.length / barCount);
      const bw = W / barCount;
      for (let i = 0; i < barCount; i++) {
        let sum = 0;
        for (let j = 0; j < binsPerBar; j++) sum += freqBuf[i * binsPerBar + j];
        const v = sum / binsPerBar / 255;
        const h = Math.max(1, v * H * 0.95);
        const x = i * bw;
        const y = H - h;
        const alpha = 0.25 + v * 0.6;
        cctx.fillStyle = `rgba(70, 220, 255, ${alpha})`;
        cctx.fillRect(x + 0.5, y, bw - 1, h);
      }

      // Waveform line (green, in front)
      cctx.lineWidth = 1;
      cctx.strokeStyle = "rgba(127, 255, 170, 0.85)";
      cctx.beginPath();
      const step = timeBuf.length / W;
      for (let x = 0; x < W; x++) {
        const sample = timeBuf[Math.floor(x * step)];
        const y = (sample / 255) * H;
        if (x === 0) cctx.moveTo(x, y);
        else cctx.lineTo(x, y);
      }
      cctx.stroke();

      requestAnimationFrame(draw);
    }
    draw();
  } catch (err: any) {
    setDiagMic(`mic error: ${err.name || ""} ${err.message || err}`);
    console.error("[mic] diag failed:", err);
  }
})();

// ---------------------------------------------------------------------------
// UI Controls
// ---------------------------------------------------------------------------

const btnWakeToggle = document.getElementById("btn-wake-toggle")!;
const wakeLabelEl = btnWakeToggle.querySelector(".wake-label")!;
const btnMenu = document.getElementById("btn-menu")!;
const menuDropdown = document.getElementById("menu-dropdown")!;
const btnRestart = document.getElementById("btn-restart")!;
const btnFixSelf = document.getElementById("btn-fix-self")!;

function applyWakeVisuals() {
  btnWakeToggle.classList.toggle("sleeping", isSleeping);
  wakeLabelEl.textContent = isSleeping ? "Sleeping" : "Active";
  document.body.classList.toggle("wake-sleeping", isSleeping);
  updateStatus(currentState);
}

function reconcileWakeControl() {
  // Only resume mic if we're actually waiting for input. If JARVIS is mid-
  // thinking or speaking, wake stays paused — it'll resume naturally when
  // audio playback finishes and the state returns to idle.
  if (isSleeping) {
    // Soft deactivation: pause the mic but preserve the wake module's
    // active flag. The user toggled sleep on purpose (e.g. mid-design to
    // narrate to a client during a demo); when they toggle back, they
    // should pick up where they left off without re-saying "ok jarvis".
    // The backend design session lives on id(ws) and the WS stays open
    // through sleep, so all conversational context survives.
    wake.pause();
  } else if (currentState === "idle" || currentState === "listening") {
    wake.resume();
  }
}

btnWakeToggle.addEventListener("click", (e) => {
  e.stopPropagation();
  isSleeping = !isSleeping;
  localStorage.setItem(WAKE_STATE_KEY, isSleeping ? "sleeping" : "active");
  applyWakeVisuals();
  reconcileWakeControl();
});

// Apply persisted visuals immediately so the button label and orb dim match
// the saved preference on every load. Mic control runs after wake.start()
// kicks in (see setTimeout below).
applyWakeVisuals();

btnMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = menuDropdown.style.display === "none" ? "block" : "none";
});

document.addEventListener("click", () => {
  menuDropdown.style.display = "none";
});

btnRestart.addEventListener("click", async (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  statusEl.textContent = "restarting...";
  try {
    await fetch("/api/restart", { method: "POST" });
    // Wait a few seconds then reload
    setTimeout(() => window.location.reload(), 4000);
  } catch {
    statusEl.textContent = "restart failed";
  }
});

btnFixSelf.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  // Activate work mode on the WebSocket session (JARVIS becomes Claude Code's voice)
  socket.send({ type: "fix_self" });
  statusEl.textContent = "entering work mode...";
});

// Settings button
const btnSettings = document.getElementById("btn-settings")!;
btnSettings.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  openSettings();
});

// First-time setup detection — check after a short delay for server readiness
setTimeout(() => {
  checkFirstTimeSetup();
}, 2000);
