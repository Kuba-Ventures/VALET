/**
 * Push-to-talk configuration (perceived-latency PR 2).
 *
 * An instant trigger ALONGSIDE the wake word: hold a key, the mic goes hot and
 * wake-word matching is skipped entirely; release to dispatch. This sidesteps
 * wake-word latency and the "hey vee"→"av" misfire class for power users.
 *
 * Opt-in and frontend-only — the enabled flag + key live in localStorage, so
 * the wake-word path is completely unaffected when it's off (the default).
 * The key is configurable via the `valet.ptt.code` key (a KeyboardEvent.code,
 * default "Space"); the Settings toggle controls enable/disable.
 *
 * Pure helpers (no DOM construction) so the routing in main.ts / wakeWord.ts
 * stays thin and the logic is reviewable in one place.
 */

export const PTT_ENABLED_KEY = "valet.ptt.enabled";
export const PTT_CODE_KEY = "valet.ptt.code";
// Option (⌥) by default — hold to talk, Wispr-style. Configurable in Settings.
export const PTT_DEFAULT_CODE = "AltLeft";

export function isPushToTalkEnabled(): boolean {
  // ⌃⌥ push-to-talk is the native default and is ALWAYS available — it's no
  // longer a user toggle (the Settings toggle now controls "Always listening"
  // / wake-word instead). Kept as a function so existing call sites are unchanged.
  return true;
}

export function setPushToTalkEnabled(on: boolean): void {
  localStorage.setItem(PTT_ENABLED_KEY, on ? "1" : "0");
}

/** The configured PTT key as a KeyboardEvent.code (default "Space"). */
export function pushToTalkCode(): string {
  return localStorage.getItem(PTT_CODE_KEY) || PTT_DEFAULT_CODE;
}

/** Human-readable label for a key code, e.g. "⌥" (Alt), "Space", "F" (KeyF). */
export function pushToTalkKeyLabel(code?: string): string {
  const c = code || pushToTalkCode();
  if (c === "AltLeft" || c === "AltRight") return "⌥";
  if (c === "MetaLeft" || c === "MetaRight") return "⌘";
  if (c === "ControlLeft" || c === "ControlRight") return "⌃";
  if (c === "ShiftLeft" || c === "ShiftRight") return "⇧";
  if (c === "Space") return "Space";
  if (c.startsWith("Key")) return c.slice(3);
  if (c.startsWith("Digit")) return c.slice(5);
  return c;
}

/**
 * True when a keyboard event should be ignored for PTT because the user is
 * typing into a field (so the PTT key doesn't hijack text entry / Settings).
 */
export function isEditableTarget(el: EventTarget | null): boolean {
  const node = el as HTMLElement | null;
  if (!node || typeof node.tagName !== "string") return false;
  const tag = node.tagName.toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return node.isContentEditable === true;
}
