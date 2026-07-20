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
 * ("ok vee what time is it"), the tail is forwarded immediately and the
 * controller transitions to active so the next utterance also flows through.
 *
 * A bare "vee" / "vee?" on its own also wakes (a soft re-engage). We require it
 * to be the whole utterance so a stray "vee" mid-sentence won't trigger, and we
 * never accept a bare single letter ("v") — too many false hits in Web Speech.
 *
 * The wake regex is derived from `assistantName` × WAKE_PREFIXES — change
 * ASSISTANT_NAME in .env, restart the backend, refresh the page, and the
 * phrase tracks. Add a new prefix by appending one string to WAKE_PREFIXES.
 */

import { createVoiceInput, type VoiceInput } from "./voice";
import type { SttCompare } from "./sttCompare";

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
  // Push-to-talk (PR 2): an instant trigger alongside the wake word. While held,
  // the mic is hot and wake-word matching is skipped; on release the captured
  // utterance is dispatched as a command. Does NOT change the wake `active` flag.
  beginPushToTalk(): void;
  endPushToTalk(): void;
  cancelPushToTalk(): void;  // abort a held turn and DISCARD captured audio
}

/**
 * A better recognizer for the push-to-talk turn only (see deepgramMic.ts).
 * When present, its transcript is PREFERRED over the built-in recognizer's; if
 * it yields nothing, the built-in segments are used instead, so a failure here
 * degrades quality rather than losing the command.
 */
export interface PushToTalkTranscriber {
  available(): boolean;
  start(): Promise<unknown>;
  stop(): Promise<string>;
  cancel(): void;
}

export interface WakeWordHandlers {
  onWake: () => void;
  // `opts.fromPushToTalk` marks a deliberate push-to-talk dispatch so the caller
  // can skip echo filtering (the user explicitly held the key).
  onCommand: (text: string, opts?: { fromPushToTalk?: boolean }) => void;
  onError: (msg: string) => void;
  // Called once a push-to-talk interaction fully finishes (after dispatch or an
  // empty release), so the caller can restore mic state (e.g. re-pause if asleep).
  onPushToTalkEnd?: () => void;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The wake-phrase prefix vocabulary. The full wake phrase is
 * `<prefix> <assistantName>` — e.g. "ok vee", "okay vee", "hey vee".
 *
 * Single source of truth for all accepted prefixes. To add a new one
 * ("yo", "hi", "hello", etc.), append one lowercase string here and the
 * regex picks it up automatically. Each entry is regex-escaped before
 * joining, so a prefix containing regex metacharacters won't break the
 * pattern.
 */
const WAKE_PREFIXES = ["ok", "okay", "hey", "yo", "hi", "hello", "hey there", "morning"] as const;

// Speech recognizers rarely transcribe the one-syllable "vee" cleanly — they
// hear "V", "B", "Bee", "Vi", "D", "E", "Z", "fee", etc. When the name is "vee"
// we match all of these so the wake phrase fires regardless of how the user
// pronounces it. These only match AFTER a wake prefix ("hey d", "ok z"), which
// is what keeps them safe: letter-sounds and rhymes essentially never follow a
// greeting in normal speech, so false wakes stay rare. DELIBERATELY EXCLUDED:
// real words that rhyme with "vee" (the, be, see, tea, pea, key, gee) — "hey the
// meeting started" must NOT wake. Single letters/rhymes are allowed here but NOT
// as a bare re-engage (VEE_SOFT_VARIANTS), where there's no prefix to gate them.
const VEE_WAKE_VARIANTS = [
  "vee", "vi", "vie", "ve",        // direct mishearings of "vee"
  "v", "b", "c", "d", "e", "g", "p", "t", "z",  // letter-sounds that rhyme with "vee"
  "bee", "fee",                    // rhyming homophones STT emits as words
];
// Bare re-engage ("vee" as the whole utterance) — no prefix gating it, so keep
// this TIGHT: only clear two-letter mishearings, never single letters or words.
const VEE_SOFT_VARIANTS = ["vee", "vi", "vie", "ve", "bee", "fee"];

// Some recognizers collapse the WHOLE "hey vee" wake phrase into a single token —
// e.g. "hey vee" comes back as "AV". These have no prefix to gate them, so they
// only count at the very START of an utterance (see buildWakeRegex), which keeps
// a mid-sentence "AV club"/"AV cable" from waking. Add full-phrase mishearings
// here, not letter-sounds (those belong in VEE_WAKE_VARIANTS, prefix-gated).
const VEE_FULL_VARIANTS = ["av"];

function nameAlternation(name: string, soft: boolean): string {
  if (name === "vee") {
    return (soft ? VEE_SOFT_VARIANTS : VEE_WAKE_VARIANTS).map(escapeRegExp).join("|");
  }
  return escapeRegExp(name);
}

function buildWakeRegex(name: string): RegExp {
  // Match any prefix from WAKE_PREFIXES followed by the name (or a mishearing of
  // it), tolerating commas/periods/other punctuation speech recognizers insert.
  const prefixAlt = WAKE_PREFIXES.map(escapeRegExp).join("|");
  const nameAlt = nameAlternation(name.toLowerCase(), false);
  const prefixed = `\\b(?:${prefixAlt})\\b[^\\w]*\\b(?:${nameAlt})\\b`;
  // Plus the whole-phrase mishearings ("av" for "hey vee"), anchored to the
  // START of the utterance so they wake the assistant and forward the tail as a
  // command, without matching the same token mid-sentence.
  const full =
    name.toLowerCase() === "vee" && VEE_FULL_VARIANTS.length
      ? `|^\\s*(?:${VEE_FULL_VARIANTS.map(escapeRegExp).join("|")})\\b`
      : "";
  return new RegExp(`(?:${prefixed})${full}`, "i");
}

/**
 * Soft re-engage: the bare name as the WHOLE utterance ("vee", "vee?", "vee.").
 * Anchored start-to-end so a stray "vee" inside a sentence won't wake. Bare
 * single letters (v/b) are excluded here to avoid Web Speech noise.
 */
function buildSoftRegex(name: string): RegExp {
  const nameAlt = nameAlternation(name.toLowerCase(), true);
  return new RegExp(`^\\s*(?:${nameAlt})\\s*[?.!]*\\s*$`, "i");
}

function normalizeName(raw: string): string {
  const cleaned = (raw || "").trim().toLowerCase();
  // Default casual name is "vee". Guard against a bare single letter (e.g. "v")
  // which would be far too trigger-happy in continuous speech recognition.
  if (!cleaned || cleaned.length < 2) return "vee";
  return cleaned;
}

export function createWakeWord(
  initialName: string,
  handlers: WakeWordHandlers,
  pttTranscriber?: PushToTalkTranscriber,
  compare?: SttCompare
): WakeWordController {
  let assistantName = normalizeName(initialName);
  let wakeRegex = buildWakeRegex(assistantName);
  let softRegex = buildSoftRegex(assistantName);
  let active = false;
  // True while a Deepgram turn is in flight for the current hold.
  let dgActive = false;
  // Set when we wake on an INTERIM transcript; the matching FINAL then carries
  // the actual command (the tail after the wake phrase).
  let wokeThisUtterance = false;

  // ── Push-to-talk state (PR 2) ──
  // `pttHeld`: the key is down. `pttFinalizing`: released, awaiting the FINAL
  // that flushes the buffered audio. Final segments accumulate in `pttSegments`
  // so a pause mid-hold (which the recognizer emits as its own FINAL) isn't lost.
  let pttHeld = false;
  let pttFinalizing = false;
  // `pttDiscarding`: a cancelled ⌃⌥ turn — swallow the trailing buffered
  // transcript (flushed by finalize) instead of dispatching it, then clear.
  let pttDiscarding = false;
  let pttSegments: string[] = [];
  let pttTimer: ReturnType<typeof setTimeout> | undefined;

  function dispatchPushToTalk(override?: string) {
    if (!pttFinalizing && !pttHeld) return;
    pttFinalizing = false;
    if (pttTimer !== undefined) { clearTimeout(pttTimer); pttTimer = undefined; }
    const cmd = (override || pttSegments.join(" ")).replace(/\s+/g, " ").trim();
    pttSegments = [];
    if (cmd) handlers.onCommand(cmd, { fromPushToTalk: true });
    handlers.onPushToTalkEnd?.();
  }

  function goPassive() {
    active = false;
  }

  function goActive() {
    if (active) return;
    active = true;
    handlers.onWake();
  }

  const voiceInput: VoiceInput = createVoiceInput(
    (text: string, isFinal: boolean) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      // A cancelled ⌃⌥ turn: drop the trailing buffered audio so a ⌃⌥-letter
      // shortcut never leaks into a dispatched command or the wake path.
      if (pttDiscarding) return;

      // A/B capture (#321), observation only. This sits ABOVE the push-to-talk
      // branch deliberately: when Deepgram wins it dispatches early, which
      // clears `pttFinalizing`, so WebKit's FINAL for the same audio arrives
      // here as an ordinary transcript. Capturing inside the branch below would
      // therefore record an empty WebKit side for exactly the turns Deepgram
      // won — the one bias that would make the comparison useless. The compare
      // window is only open across a hold plus its settle delay, so nothing
      // outside a push-to-talk turn is collected.
      if (isFinal) compare?.webkitFinal(trimmed);

      // Push-to-talk owns the transcript stream while held or finalizing —
      // wake-word matching is skipped entirely. Accumulate FINAL segments;
      // dispatch when the release-triggered FINAL arrives.
      if (pttHeld || pttFinalizing) {
        if (isFinal) {
          pttSegments.push(trimmed);
          if (pttFinalizing) dispatchPushToTalk();
        }
        return;
      }

      const extractTail = (t: string): string => {
        const m = wakeRegex.exec(t);
        return m ? t.slice(m.index + m[0].length).replace(/^[^\w]+/, "").trim() : t;
      };

      if (active) {
        // In conversation: only FINAL transcripts are commands — interim ones
        // are mid-sentence and would be incomplete.
        if (!isFinal) return;
        if (wokeThisUtterance) {
          // We woke mid-utterance on an interim; the command is the tail of
          // this final after the wake phrase.
          wokeThisUtterance = false;
          const cmd = extractTail(trimmed);
          if (cmd) handlers.onCommand(cmd);
        } else {
          handlers.onCommand(trimmed);
        }
        return;
      }

      // Asleep: check the wake phrase on EVERY transcript (interim included) so
      // the orb wakes the instant you say "hey vee", not after the pause.
      if (wakeRegex.test(trimmed)) {
        goActive();
        if (isFinal) {
          // Final already has the full utterance — fire the command now.
          wokeThisUtterance = false;
          const tail = extractTail(trimmed);
          if (tail) handlers.onCommand(tail);
        } else {
          // Woke early on interim; the FINAL will carry the command.
          wokeThisUtterance = true;
        }
        return;
      }

      // Soft re-engage: a bare "vee" / "vee?" as the whole utterance (final only).
      if (isFinal && softRegex.test(trimmed)) {
        goActive();
        wokeThisUtterance = false;
      }
    },
    (msg: string) => handlers.onError(msg)
  );

  return {
    start() { voiceInput.start(); },
    stop() { goPassive(); voiceInput.stop(); },
    // pause() = "temporarily stop hearing"; keeps active flag so a brief mic-off
    // during Vee's own TTS doesn't drop us out of an in-progress conversation.
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
      softRegex = buildSoftRegex(assistantName);
      goPassive();
    },
    notifyCommandComplete() { /* no-op in continuous mode; kept for API stability */ },
    getName() { return assistantName; },
    getWakePhrase() { return `ok ${assistantName}`; },
    isActive() { return active; },
    beginPushToTalk() {
      if (pttHeld) return;
      pttHeld = true;
      pttFinalizing = false;
      pttSegments = [];
      if (pttTimer !== undefined) { clearTimeout(pttTimer); pttTimer = undefined; }
      voiceInput.resume();   // ensure the mic is hot even if asleep/paused
      // Run the better recognizer ALONGSIDE the built-in one for this turn. Both
      // capture; whichever produces text wins at dispatch, so a Deepgram hiccup
      // costs accuracy, not the command.
      dgActive = false;
      if (pttTranscriber?.available()) {
        dgActive = true;
        void pttTranscriber.start().catch(() => { dgActive = false; });
      }
      // Observation only (#321): record which recognizer heard what. Both are
      // already running on the same audio; this just stops discarding the loser.
      compare?.begin(dgActive);
      handlers.onWake();     // visual cue: show we're listening (no change to `active`)
    },
    endPushToTalk() {
      if (!pttHeld) return;
      pttHeld = false;
      pttFinalizing = true;
      voiceInput.finalize();  // flush buffered audio → a FINAL arrives, then dispatch
      if (dgActive && pttTranscriber) {
        dgActive = false;
        // Prefer the better transcript, but never block on it: the timer below
        // still fires if Deepgram is slow or silent.
        void pttTranscriber.stop().then((text) => {
          const t = (text || "").trim();
          compare?.deepgramFinal(t);
          if (t && pttFinalizing) dispatchPushToTalk(t);
        }).catch(() => {
          compare?.deepgramFinal("");   // a Deepgram failure counts as a miss
          /* fall through to the built-in transcript */
        });
      }
      compare?.end();
      // Fallback: if no FINAL lands promptly, dispatch whatever we captured.
      if (pttTimer !== undefined) clearTimeout(pttTimer);
      pttTimer = setTimeout(() => { if (pttFinalizing) dispatchPushToTalk(); }, 1200);
    },
    cancelPushToTalk() {
      // Abort a held turn WITHOUT dispatching — used when the global ⌃⌥ hold was
      // actually a ⌃⌥-letter shortcut (a non-modifier key landed mid-hold).
      if (!pttHeld && !pttFinalizing) return;
      pttHeld = false;
      pttFinalizing = false;
      pttSegments = [];
      pttDiscarding = true;
      compare?.cancel();   // a ⌃⌥-shortcut is not speech — no A/B sample here
      if (dgActive) { dgActive = false; pttTranscriber?.cancel(); }
      if (pttTimer !== undefined) clearTimeout(pttTimer);
      // Flush the recognizer buffer (mic keeps listening — onend restarts it),
      // then swallow that trailing FINAL and re-open the normal wake path.
      voiceInput.finalize();
      pttTimer = setTimeout(() => { pttDiscarding = false; pttTimer = undefined; }, 1500);
    },
  };
}
