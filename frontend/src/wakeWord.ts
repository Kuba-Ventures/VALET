/**
 * Wake-word gating layer over continuous speech recognition.
 *
 * Modes:
 *   - passive: transcripts are scanned for any "<prefix> <name>" combination
 *     where <prefix> is one of WAKE_PREFIXES below. The wake phrase itself
 *     is not forwarded.
 *   - active: every final transcript is forwarded as a command. The controller
 *     stays active indefinitely — only `pause()` / `stop()` (e.g. the Sleeping
 *     toggle) returns it to passive.
 *
 * If the wake phrase and command arrive in the same utterance
 * ("ok jarvis what time is it"), the tail is forwarded immediately and the
 * controller transitions to active so the next utterance also flows through.
 *
 * The wake regex is derived from `assistantName` × WAKE_PREFIXES — change
 * ASSISTANT_NAME in .env, restart the backend, refresh the page, and the
 * phrase tracks. Add a new prefix by appending one string to WAKE_PREFIXES.
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

/**
 * The wake-phrase prefix vocabulary. The full wake phrase is
 * `<prefix> <assistantName>` — e.g. "ok jarvis", "okay jarvis", "hey jarvis".
 *
 * Single source of truth for all accepted prefixes. To add a new one
 * ("yo", "hi", "hello", etc.), append one lowercase string here and the
 * regex picks it up automatically. Each entry is regex-escaped before
 * joining, so a prefix containing regex metacharacters won't break the
 * pattern.
 */
const WAKE_PREFIXES = ["ok", "okay", "hey"] as const;

function buildWakeRegex(name: string): RegExp {
  // Match any prefix from WAKE_PREFIXES followed by the name, tolerating
  // commas/periods/other punctuation that speech recognizers occasionally
  // insert between the prefix and the name.
  const prefixAlt = WAKE_PREFIXES.map(escapeRegExp).join("|");
  return new RegExp(
    `\\b(?:${prefixAlt})\\b[^\\w]*\\b${escapeRegExp(name.toLowerCase())}\\b`,
    "i",
  );
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
    // reset() = "go back to needing the wake phrase". No longer called from
    // the Sleeping toggle (which now preserves active state so demos can
    // pause mid-conversation), but kept for explicit teardown (e.g. setName).
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
