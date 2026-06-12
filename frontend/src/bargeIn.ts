/**
 * Barge-in detection (UC5).
 *
 * When Vee is speaking, the mic stays live so the user can talk over the TTS,
 * redirect, or stop mid-stream. The hard part is telling a real interruption
 * from the recognizer hearing Vee's own voice (echo). `shouldBargeIn` is the
 * pure decision:
 *
 *   - explicit interrupt words ("stop", "wait", "no", "actually", …) ALWAYS
 *     barge in — Vee doesn't say these mid-reply, so they're safe even over TTS;
 *   - otherwise, if the heard text mostly overlaps what Vee is currently saying,
 *     it's treated as echo and ignored;
 *   - a substantive non-echo utterance barges in.
 *
 * Kept pure (no DOM/audio) so the policy is testable and lives in one place.
 */

// Interrupt words/phrases that always cut Vee off. Matched at the START of the
// utterance so "no, the other one" and "stop reading" both trigger.
export const BARGE_KEYWORDS = [
  "stop", "wait", "hold on", "hang on", "cancel", "no", "nope",
  "actually", "never mind", "nevermind", "forget it", "shut up", "quiet",
];

function norm(s: string): string {
  return (s || "").toLowerCase().replace(/[.,!?；;]/g, " ").replace(/\s+/g, " ").trim();
}

/** Fraction of `heard`'s words that appear in `spoken` (echo overlap, 0..1). */
function echoOverlap(heard: string, spoken: string): number {
  const sp = norm(spoken);
  if (!sp) return 0;
  const hw = norm(heard).split(" ").filter(Boolean);
  if (!hw.length) return 0;
  const hit = hw.filter((w) => w.length > 1 && sp.includes(w)).length;
  return hit / hw.length;
}

/**
 * Decide whether a transcript heard WHILE Vee is speaking is a real barge-in.
 * `spoken` is the text Vee is currently saying (empty when Vee is silent, e.g.
 * mid-"thinking", where any utterance is a real interruption).
 */
export function shouldBargeIn(heard: string, spoken: string): boolean {
  const h = norm(heard);
  if (!h) return false;

  // Explicit interrupt keywords — always cut in (start-anchored).
  for (const k of BARGE_KEYWORDS) {
    if (h === k || h.startsWith(k + " ")) return true;
  }

  // No TTS playing (e.g. "thinking") → any heard speech is a real interruption.
  if (!norm(spoken)) return h.split(" ").length >= 1;

  // Mostly an echo of what Vee is saying → ignore.
  if (echoOverlap(h, spoken) > 0.6) return false;

  // A substantive, non-echo utterance barges in; ignore one-word noise.
  return h.split(" ").length >= 2;
}
