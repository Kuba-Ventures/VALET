/**
 * Wake-word gating layer over continuous speech recognition.
 *
 * Modes:
 *   - passive: transcripts are scanned for "ok <name>" or "okay <name>".
 *     The wake phrase itself is not forwarded.
 *   - active: every final transcript is forwarded as a command. The controller
 *     stays active indefinitely — only `pause()` / `stop()` (e.g. the Sleeping
 *     toggle) returns it to passive.
 *
 * If the wake phrase and command arrive in the same utterance
 * ("ok jarvis what time is it"), the tail is forwarded immediately and the
 * controller transitions to active so the next utterance also flows through.
 *
 * The wake regex is derived from `assistantName` — change ASSISTANT_NAME in
 * .env, restart the backend, refresh the page, and the phrase tracks.
 */

import { createVoiceInput, type VoiceInput } from "./voice";

export interface WakeWordController {
  start(): void;
  stop(): void;
  pause(): void;   // temporary mic-off (e.g. during TTS); preserves active flag
  resume(): void;
  reset(): void;   // forces back to passive (active = false) without stopping the mic
  setName(name: string): void;
  notifyCommandComplete(): void;
  getName(): string;
  getWakePhrase(): string;
  isActive(): boolean;
}

export interface WakeWordHandlers {
  onWake: () => void;
  onCommand: (text: string) => void;
  onError: (msg: string) => void;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildWakeRegex(name: string): RegExp {
  // Match "ok" or "okay" followed by the name, tolerating commas/periods/
  // other punctuation that speech recognizers occasionally insert.
  return new RegExp(`\\bok(?:ay)?\\b[^\\w]*\\b${escapeRegExp(name.toLowerCase())}\\b`, "i");
}

function normalizeName(raw: string): string {
  const cleaned = (raw || "").trim().toLowerCase();
  return cleaned || "jarvis";
}

export function createWakeWord(
  initialName: string,
  handlers: WakeWordHandlers
): WakeWordController {
  let assistantName = normalizeName(initialName);
  let wakeRegex = buildWakeRegex(assistantName);
  let active = false;

  function goPassive() {
    active = false;
  }

  function goActive() {
    if (active) return;
    active = true;
    handlers.onWake();
  }

  const voiceInput: VoiceInput = createVoiceInput(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      if (active) {
        // Continuous conversation: every transcript is forwarded as a command.
        // Stay active until pause()/stop() is called (i.e. Sleeping toggle).
        handlers.onCommand(trimmed);
        return;
      }

      const match = wakeRegex.exec(trimmed);
      if (!match) return;

      const tail = trimmed.slice(match.index + match[0].length).replace(/^[^\w]+/, "").trim();
      goActive();
      if (tail) {
        // Wake phrase plus command in one utterance — fire the tail too.
        handlers.onCommand(tail);
      }
    },
    (msg: string) => handlers.onError(msg)
  );

  return {
    start() { voiceInput.start(); },
    stop() { goPassive(); voiceInput.stop(); },
    // pause() = "temporarily stop hearing"; keeps active flag so a brief mic-off
    // during JARVIS's own TTS doesn't drop us out of an in-progress conversation.
    pause() { voiceInput.pause(); },
    resume() { voiceInput.resume(); },
    // reset() = "go back to needing the wake phrase". Called by the Sleeping
    // toggle so flipping Sleeping → Active always starts in passive mode.
    reset() { goPassive(); },
    setName(name: string) {
      const next = normalizeName(name);
      if (next === assistantName) return;
      assistantName = next;
      wakeRegex = buildWakeRegex(assistantName);
      goPassive();
    },
    notifyCommandComplete() { /* no-op in continuous mode; kept for API stability */ },
    getName() { return assistantName; },
    getWakePhrase() { return `ok ${assistantName}`; },
    isActive() { return active; },
  };
}
