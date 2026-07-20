/**
 * Deepgram-vs-WebKit A/B capture for the push-to-talk turn.
 *
 * Issue #321 gates Deepgram behind a per-license spend cap, but that build is
 * only worth doing if Deepgram is actually better for this user's accented
 * speech — which #320 left unverified. This module answers that question with
 * the data the app is ALREADY producing and discarding.
 *
 * Both recognizers already run on identical audio for every hold (see
 * `wakeWord.ts` — "Run the better recognizer ALONGSIDE the built-in one").
 * WebKit's transcript is simply overwritten when Deepgram returns text. So the
 * comparison needs no extra capture, no extra Deepgram spend, and no second
 * utterance from the user: it just stops throwing the loser away.
 *
 * OBSERVATION ONLY. Nothing here influences which transcript is dispatched. If
 * every function in this file threw, push-to-talk would behave identically.
 *
 * Why the settle delay: Deepgram usually returns BEFORE WebKit's final lands
 * (that is the point — it dispatches early). Emitting at dispatch time would
 * therefore record an empty WebKit side for exactly the turns where Deepgram
 * won, biasing the result toward Deepgram. So a hold is emitted only after both
 * sides have had a chance to report.
 */

/** One push-to-talk turn, with whatever each recognizer heard. */
export interface SttPair {
  /** Monotonic id for the hold, so late arrivals attach to the right turn. */
  hold: number;
  /** Milliseconds the chord was held — the billable quantity for Deepgram. */
  held_ms: number;
  /** What the built-in WebKit recognizer heard ("" if it produced nothing). */
  webkit: string;
  /** What Deepgram Nova heard ("" if unavailable, silent, or errored). */
  deepgram: string;
  /** False when Deepgram never started for this hold (no key / start failed). */
  deepgram_ran: boolean;
}

// Both sides get this long after key release to report before the pair is
// emitted. Comfortably past deepgramMic's own 1500ms FINAL_GRACE_MS so a slow
// Deepgram final is recorded rather than silently counted as a miss.
const SETTLE_MS = 2500;

export interface SttCompare {
  /** Called when the chord goes down. `dgRan` = Deepgram started for this turn. */
  begin(dgRan: boolean): void;
  /** Called for each WebKit FINAL segment observed while held or finalizing. */
  webkitFinal(text: string): void;
  /** Called with Deepgram's transcript for the turn (may be ""). */
  deepgramFinal(text: string): void;
  /** Called when the chord is released — starts the settle timer. */
  end(): void;
  /** Called when the hold was a shortcut, not speech — discards the turn. */
  cancel(): void;
}

/**
 * `emit` receives each completed pair. It is invoked at most once per hold, and
 * never for a cancelled hold. Pairs where BOTH sides are empty are dropped —
 * those are silent holds and carry no signal about relative accuracy.
 */
export function createSttCompare(emit: (pair: SttPair) => void): SttCompare {
  let hold = 0;
  let startedAt = 0;
  let heldMs = 0;
  let webkitSegments: string[] = [];
  let deepgram = "";
  let deepgramRan = false;
  let open = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function clearTimer() {
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
  }

  function flush() {
    clearTimer();
    if (!open) return;
    open = false;
    const webkit = webkitSegments.join(" ").replace(/\s+/g, " ").trim();
    const dg = deepgram.replace(/\s+/g, " ").trim();
    webkitSegments = [];
    deepgram = "";
    // A hold where neither recognizer heard anything says nothing about which
    // is better — it is a mis-hold or silence. Recording it would dilute the
    // disagreement rate with noise.
    if (!webkit && !dg) return;
    emit({ hold, held_ms: heldMs, webkit, deepgram: dg, deepgram_ran: deepgramRan });
  }

  return {
    begin(dgRan: boolean) {
      // A begin without a matching end (shouldn't happen, but the chord is an
      // OS-level event tap) must not strand the previous turn's timer.
      clearTimer();
      hold += 1;
      startedAt = Date.now();
      heldMs = 0;
      webkitSegments = [];
      deepgram = "";
      deepgramRan = dgRan;
      open = true;
    },

    webkitFinal(text: string) {
      if (!open) return;
      const t = (text || "").trim();
      if (t) webkitSegments.push(t);
    },

    deepgramFinal(text: string) {
      if (!open) return;
      deepgram = text || "";
    },

    end() {
      if (!open) return;
      heldMs = Date.now() - startedAt;
      clearTimer();
      timer = setTimeout(flush, SETTLE_MS);
    },

    cancel() {
      clearTimer();
      open = false;
      webkitSegments = [];
      deepgram = "";
    },
  };
}

/**
 * True when the two transcripts differ in a way worth a human's attention.
 *
 * Case, punctuation, and whitespace differences are pure formatting — Deepgram
 * runs `smart_format`/`punctuate` and WebKit does not, so comparing raw strings
 * would report a disagreement on essentially every single turn and bury the
 * real substitutions ("GitHub" -> "ghetto") that the comparison exists to find.
 */
export function isSubstantiveDisagreement(a: string, b: string): boolean {
  const norm = (s: string) =>
    (s || "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
  return norm(a) !== norm(b);
}
