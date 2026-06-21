/**
 * VALET — Main entry point.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 */

import { createOrb, type OrbState } from "./orb";
import { createAudioPlayer } from "./voice";
import { createWakeWord } from "./wakeWord";
import { isPushToTalkEnabled, isEditableTarget } from "./pushToTalk";
import { createSocket } from "./ws";
import { openSettings, checkFirstTimeSetup } from "./settings";
import { maybeShowOnboarding } from "./onboarding";
import { createProcessPanel, type ProcessEvent } from "./processPanel";
import { createDesignPanel, type DesignEvent } from "./designPanel";
import { createConfirmCard, type ConfirmRequest } from "./confirmCard";
import { shouldBargeIn } from "./bargeIn";
import "./style.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type State = "idle" | "listening" | "thinking" | "speaking";
let currentState: State = "idle";

// UC5 barge-in: the text Vee is currently speaking, so the mic (kept live during
// TTS) can tell a real interruption from hearing Vee's own voice (echo).
let currentSpokenText = "";

// Echo cooldown: the recognizer can emit Vee's LAST words a beat after the audio
// ends, when state is already idle and the live echo filter no longer applies.
// Keep the last spoken text briefly so that tail echo is still recognized and
// dropped — without this Vee hears itself and loops ("Do you have an actual
// question?" → mic hears "actual question" → replies again → …).
let lastSpokenText = "";
let lastSpokeAt = 0;
const ECHO_COOLDOWN_MS = 1500;

// Hard-stop mute. Distinct from isSleeping: PTT mode keeps isSleeping=true (wake
// mic off) but Vee must STILL speak its replies. Only Escape/hard-stop mutes
// audio; cleared on the next user action (PTT press or a new command). Without
// this, PTT mode (isSleeping=true by default) silenced Vee entirely.
let muted = false;

// Wake-word listening toggle. Persisted across reloads via localStorage so the
// user's preference survives a refresh. Controls only the frontend's wake-word
// listening — the backend service is unaffected.
const WAKE_STATE_KEY = "valet.wakeListening";
// Push-to-talk is now the PRIMARY mode: by default the always-on wake mic is OFF
// (no wake-word latency, no self-echo) and you hold ⌥ to talk. The toggle flips
// to "Always listening" (wake-word). Default to PTT mode unless the user has
// explicitly chosen "active".
let isSleeping = localStorage.getItem(WAKE_STATE_KEY) !== "active";

const statusEl = document.getElementById("status-text")!;
const errorEl = document.getElementById("error-text")!;
const replyEl = document.getElementById("valet-reply")!;

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
  // PTT mode keeps isSleeping=true (wake mic off), but holding ⌥ still listens —
  // so show the active-state label ("listening…"/"thinking…"); only blank when
  // genuinely idle/asleep (then the "press ⌥ to talk" hint carries the cue).
  if (isSleeping && state === "idle") {
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

// Live "what VALET is doing" panel. Hidden until the first event arrives.
const processPanel = createProcessPanel();
const designPanel = createDesignPanel();

// Tier 1 confirmation card (Stage D) — appears when a risky action needs approval.
const confirmCard = createConfirmCard();

// Global kill switch — always available; halts in-progress actions + the loop.
const killBtn = document.createElement("button");
killBtn.className = "kill-switch hidden"; // hidden until VALET is actively working
killBtn.type = "button";
killBtn.textContent = "■ STOP";
document.body.appendChild(killBtn);
let killEngaged = false;
function updateKillVisibility() {
  // STOP only appears while VALET is actively working (thinking/speaking) or
  // already engaged — no clutter while idle or just listening.
  const active = currentState === "thinking" || currentState === "speaking";
  killBtn.classList.toggle("hidden", !(active || killEngaged));
}
function setKillEngaged(on: boolean) {
  killEngaged = on;
  killBtn.classList.toggle("engaged", on);
  killBtn.textContent = on ? "■ STOPPED — reset" : "■ STOP";
  if (on) {
    audioPlayer.stop();
    confirmCard.hide();
  }
  updateKillVisibility();
}
killBtn.addEventListener("click", async () => {
  const url = killEngaged ? "/api/safety/kill/reset" : "/api/safety/kill";
  setKillEngaged(!killEngaged); // optimistic; server also broadcasts kill_state
  try { await fetch(url, { method: "POST" }); } catch { /* ignore */ }
});

// Cursor-takeover indicator — a prominent banner shown whenever Vee is steering
// the real macOS cursor (a glide-and-click). The in-app half of the cursor
// follower; the on-desktop custom dot is the native-overlay follow-up.
const cursorBanner = document.createElement("div");
cursorBanner.className = "cursor-takeover hidden";
cursorBanner.innerHTML = `<span class="ct-dot"></span><span class="ct-label">Vee is steering your cursor</span>`;
document.body.appendChild(cursorBanner);
const ctLabel = cursorBanner.querySelector(".ct-label") as HTMLElement;
let ctHideTimer: number | null = null;
function showCursorTakeover(active: boolean, label?: string) {
  if (ctHideTimer) { clearTimeout(ctHideTimer); ctHideTimer = null; }
  if (active) {
    ctLabel.textContent = label ? `Vee is steering your cursor → ${label}` : "Vee is steering your cursor";
    cursorBanner.classList.remove("hidden");
  } else {
    // Linger briefly so a fast glide (~0.5s) still registers visually.
    ctHideTimer = window.setTimeout(() => cursorBanner.classList.add("hidden"), 700);
  }
}

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
  updateKillVisibility();

  switch (newState) {
    case "idle":
      if (!isSleeping) wake.resume();
      break;
    case "listening":
      if (!isSleeping) wake.resume();
      break;
    case "thinking":
    case "speaking":
      // UC5 barge-in: keep the mic LIVE while Vee thinks/speaks so the user can
      // talk over the TTS, redirect, or stop mid-stream. Echo (Vee hearing its
      // own voice) is filtered in wake.onCommand via shouldBargeIn().
      if (!isSleeping) wake.resume();
      break;
  }
}

// ---------------------------------------------------------------------------
// Wake-word-gated voice input
// ---------------------------------------------------------------------------

const wake = createWakeWord(
  "vee", // casual wake word; overwritten once /api/config resolves below
  {
    onWake: () => {
      muted = false; // a new interaction un-mutes a prior hard-stop
      transition("listening");
    },
    onCommand: (text: string, opts?: { fromPushToTalk?: boolean }) => {
      muted = false; // a dispatched command clears any prior hard-stop mute
      // UC5 barge-in: while Vee is mid-reply or mid-task the mic is live, so
      // filter echoes — only a real interruption cuts in, and it tells the
      // backend to cancel the in-flight reply/loop before the new command runs.
      // A push-to-talk dispatch is deliberate (the user held a key), so it skips
      // the echo filter but still cancels any in-flight reply.
      if (currentState === "speaking" || currentState === "thinking") {
        if (!opts?.fromPushToTalk && !shouldBargeIn(text, currentSpokenText)) return; // echo / noise — ignore
        socket.send({ type: "barge_in" });
      } else if (!opts?.fromPushToTalk && lastSpokenText && Date.now() - lastSpokeAt < ECHO_COOLDOWN_MS) {
        // Just-after-speech: filter the tail echo against what Vee just said. A
        // real new command (low overlap / interrupt keyword) still passes; an
        // echo of Vee's own words is dropped, breaking the self-loop.
        if (!shouldBargeIn(text, lastSpokenText)) return;
      }
      audioPlayer.stop();
      currentSpokenText = "";
      const sent = socket.send({ type: "transcript", text, isFinal: true });
      if (sent) {
        audioPlayer.markTurnStart(); // perceived-latency: time until first audible reply
        transition("thinking");
      } else {
        showError("Backend not connected. Reconnecting…");
        transition("idle");
      }
    },
    onPushToTalkEnd: () => {
      // Push-to-talk finished — restore the normal mic state (re-pauses the mic
      // if the assistant is asleep, so a PTT-while-asleep doesn't leave it hot).
      reconcileWakeControl();
    },
    onError: (msg: string) => {
      showError(msg);
    },
  }
);

const valetLabelEl = document.getElementById("valet-label")!;
// The orb label is the formal brand (VALET); the spoken wake word is the casual
// name (Vee). They are deliberately decoupled.
valetLabelEl.textContent = "VALET";

function applyAssistantName(name: string) {
  wake.setName(name);
}

// Default wake name; updated once /api/config resolves.
applyAssistantName("vee");

// Pull the configured wake name from the backend; if the fetch fails we keep "vee".
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
  lastSpokenText = currentSpokenText;  // remember for the post-speech echo cooldown
  lastSpokeAt = Date.now();
  currentSpokenText = ""; // live echo filter no longer applies; cooldown takes over
  // In active conversation, stay visually in "listening" between turns so the
  // user can tell the assistant is still hot. Drops back to "idle" only when
  // the wake module has been put back to passive (e.g. via Sleeping toggle).
  transition(wake.isActive() ? "listening" : "idle");
  // Audio finishing means VALET just wrapped a reply — arm the quiet-timer.
  // Trailing background events will reset (not kill) it; see
  // refreshPanelAutoClose. Force-closes once the stream goes quiet.
  refreshPanelAutoClose();
});

// ---------------------------------------------------------------------------
// Panel auto-close on idle
// ---------------------------------------------------------------------------
// User asked: when VALET finishes a turn, don't leave the process / design
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
    // Hard stop / sleep: never play audio while asleep. Drops any TTS frames the
    // backend sent in the window before its barge_in cancel took effect, so
    // Escape silences Vee instantly regardless of backend timing. Gated on
    // `muted` (hard-stop), NOT isSleeping — PTT mode is isSleeping=true but must
    // still play replies.
    if (muted) return;
    const audioData = msg.data as string;
    console.log("[audio] received", audioData ? `${audioData.length} chars` : "EMPTY", "state:", currentState);
    if (audioData) {
      // Remember what Vee is saying so the live mic can distinguish a real
      // barge-in from hearing the TTS itself (UC5).
      if (typeof msg.text === "string" && msg.text) currentSpokenText = msg.text;
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
      console.log("[VALET]", msg.text);
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
    console.log("[VALET]", msg.text);
    if (msg.text) showReply(msg.text as string);
  } else if (type === "ptt") {
    // Global hold-⌃⌥ chord from the backend's system-wide event tap — fires even
    // when another app is focused (the in-window keydown can't). Mirror the local
    // PTT path; begin/end/cancel are idempotent, so the double-fire while VALET
    // itself is focused is harmless. Honor the same enable toggle.
    if (isPushToTalkEnabled()) {
      if (msg.state === "down") wake.beginPushToTalk();
      else if (msg.state === "up") wake.endPushToTalk();
      else if (msg.state === "cancel") wake.cancelPushToTalk();  // ⌃⌥-letter shortcut → discard
    }
  } else if (type === "task_spawned") {
    console.log("[task]", "spawned:", msg.task_id, msg.prompt);
  } else if (type === "task_complete") {
    console.log("[task]", "complete:", msg.task_id, msg.status, msg.summary);
  } else if (type === "process_event") {
    // ProcessEventBus broadcasts — drive the live activity panel.
    const event = msg.event as ProcessEvent | undefined;
    if (event?.type === "cursor_control") {
      const p = (event as { payload?: { active?: boolean; label?: string } }).payload;
      showCursorTakeover(!!p?.active, p?.label);
    }
    if (event) processPanel.handleEvent(event);
  } else if (type === "close_panel") {
    // Server-side voice intent ("close it", "dismiss", etc.) closes the panel.
    processPanel.close();
  } else if (type === "confirm_request") {
    // Tier 1 action awaiting approval — show the confirm card, reply with the choice.
    const req = msg as unknown as ConfirmRequest;
    confirmCard.show(req, (allow) => {
      socket.send({ type: "confirm_response", id: req.id, allow });
    });
  } else if (type === "kill_state") {
    setKillEngaged(Boolean((msg as { engaged?: boolean }).engaged));
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
  // the state this message produced. While VALET is actively working the
  // foreground turn (thinking/speaking) the panel stays. Once the turn has
  // wrapped (idle/listening) each subsequent message — including trailing
  // background process_events — RESETS a short quiet-timer; when the stream
  // finally goes quiet the watchdog force-closes via tryAutoClose. This is
  // the safety net that survives a dropped task_done or a lingering result
  // card (both of which otherwise wedge the panel open forever).
  refreshPanelAutoClose();
});

function refreshPanelAutoClose() {
  // Keep the coarse idle-close OFF while VALET is actively working the
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
// mousedown fires even when the press starts a whole-window drag (where the
// drag region suppresses the subsequent click), so grabbing the orb to move it
// still satisfies the autoplay gesture and unlocks TTS audio.
document.addEventListener("mousedown", ensureAudioContext);
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

// ── Push-to-talk (PR 2): hold the configured key to talk instantly, skipping
// the wake word. Opt-in via Settings (default off), so the wake-word path is
// untouched when disabled. Guards: ignore auto-repeat, typing in fields, and
// an open Settings panel; preventDefault stops Space scrolling / activating a
// focused button while held.
function pttSettingsOpen(): boolean {
  return !!document.getElementById("settings-panel-inner")?.classList.contains("open");
}
// PTT is the ⌃⌥ (Control+Option) chord — identical to the global system-wide tap
// (global_ptt.py), so the gesture is the same whether VALET is focused (this
// handler) or another app is (the backend tap). Begin when BOTH are held; a
// non-modifier key mid-hold is a real ⌃⌥-letter shortcut, so cancel and discard.
let pttChordActive = false;
let pttChordCancelled = false;
const isModifierKey = (k: string) =>
  k === "Control" || k === "Alt" || k === "Meta" || k === "Shift";
document.addEventListener("keydown", (e) => {
  if (!isPushToTalkEnabled() || isEditableTarget(e.target) || pttSettingsOpen()) return;
  if (pttChordActive) {
    if (!pttChordCancelled && !isModifierKey(e.key)) {
      pttChordCancelled = true;
      wake.cancelPushToTalk();  // ⌃⌥-letter shortcut → drop the captured audio
    }
    return;
  }
  if (e.ctrlKey && e.altKey) {
    pttChordActive = true;
    pttChordCancelled = false;
    e.preventDefault();
    wake.beginPushToTalk();
  }
}, { capture: true });
document.addEventListener("keyup", (e) => {
  if (!pttChordActive) return;
  // The chord ends as soon as either Control or Option is released.
  if (!e.ctrlKey || !e.altKey) {
    const wasCancelled = pttChordCancelled;
    pttChordActive = false;
    pttChordCancelled = false;
    e.preventDefault();
    if (!wasCancelled) wake.endPushToTalk();
  }
}, { capture: true });
// Releasing focus / switching apps while held must not strand the mic "open".
window.addEventListener("blur", () => {
  pttChordActive = false;
  pttChordCancelled = false;
  wake.endPushToTalk();
});

// Global ⌃⌥ chord from the native Rust listener (works from ANY app, even when
// VALET isn't focused). The Rust tap evals this hook; mirror the in-window path.
(window as unknown as { __valetChord?: (s: string) => void }).__valetChord = (state: string) => {
  if (!isPushToTalkEnabled()) return;
  if (state === "down") wake.beginPushToTalk();
  else if (state === "up") wake.endPushToTalk();
  else if (state === "cancel") wake.cancelPushToTalk();
};

// Show a "hold ⌃⌥ to talk" affordance under the status line whenever the
// push-to-talk toggle is on. Updates live when the Settings toggle flips
// (settings.ts dispatches "ptt-changed").
const pttHintEl = document.getElementById("ptt-hint")!;
function updatePttHint() {
  const on = isPushToTalkEnabled();
  pttHintEl.textContent = "hold ⌃⌥ to talk";
  pttHintEl.classList.toggle("hidden", !on);
}
window.addEventListener("ptt-changed", updatePttHint);
updatePttHint();

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

function applyWakeVisuals() {
  const ptt = isPushToTalkEnabled();
  const pttMode = isSleeping && ptt;       // wake mic off, but hold-⌥ ready
  const trulyAsleep = isSleeping && !ptt;  // no wake, no PTT → fully off
  btnWakeToggle.classList.toggle("sleeping", trulyAsleep);
  btnWakeToggle.classList.toggle("ptt-mode", pttMode);
  wakeLabelEl.textContent = !isSleeping
    ? "Always listening"
    : (ptt ? "Push-to-talk" : "Sleeping");
  // Only the truly-off state dims the orb — PTT mode is READY, not asleep.
  document.body.classList.toggle("wake-sleeping", trulyAsleep);
  updatePttHint();
  updateStatus(currentState);
}

function reconcileWakeControl() {
  // Only resume mic if we're actually waiting for input. If VALET is mid-
  // thinking or speaking, wake stays paused — it'll resume naturally when
  // audio playback finishes and the state returns to idle.
  if (isSleeping) {
    // Soft deactivation: pause the mic but preserve the wake module's
    // active flag. The user toggled sleep on purpose (e.g. mid-design to
    // narrate to a client during a demo); when they toggle back, they
    // should pick up where they left off without re-saying "ok valet".
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

// ── Hard stop (Escape) ──────────────────────────────────────────────────────
// A GUARANTEED manual interrupt that does not depend on voice/barge-in
// detection: kill the TTS instantly, cancel any in-flight reply or UC task
// (incl. a cursor glide — the backend barge_in handler calls _uc_task.cancel()),
// and put VALET to sleep so it stops listening until you wake it. Important now
// that VALET can move the real cursor: one key always takes back control.
function hardStop() {
  muted = true;                                     // 0. mute audio until next user action
  audioPlayer.stop();                               // 1. kill TTS now (client-side)
  try { socket.send({ type: "barge_in" }); } catch { /* ignore */ }  // 2. cancel reply + UC task
  if (!isSleeping) {                                 // 3. sleep: mic off, won't act until woken
    isSleeping = true;
    localStorage.setItem(WAKE_STATE_KEY, "sleeping");
    applyWakeVisuals();
    reconcileWakeControl();
  }
}
// Make it reachable on the existing kill-switch button too: a long-press / the
// button stays the safety affordance, but Escape is the always-on hotkey.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  // Let an open confirm card keep Escape (it cancels the pending action), and
  // never steal it from a focused text field.
  if (confirmCard.isOpen() || isEditableTarget(e.target)) return;
  e.preventDefault();
  hardStop();
}, { capture: true });

// The three-dot button opens Settings directly. The old dropdown's other items
// (Restart Server, Fix Yourself) were dev-only and have been removed from the
// user-facing UI.
btnMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  openSettings();
});
// Settings now opens from the menu-bar tray (main.rs evals this hook); the
// in-orb three-dots button is hidden in CSS in favor of that.
(window as unknown as { __valetOpenSettings?: () => void }).__valetOpenSettings = () => {
  openSettings();
};

// First-time setup detection — check after a short delay for server readiness.
// Onboarding is the first-run flow; only fall back to the Settings setup-mode
// auto-open when the wizard is NOT showing, so the two don't stack (which left
// a stale setup-mode Settings panel behind the wizard).
setTimeout(async () => {
  const onboarding = await maybeShowOnboarding();
  if (!onboarding) checkFirstTimeSetup();
}, 2000);
