# Point-and-Teach — Scoping

**Date:** 2026-06-18 · **Status:** Scope / decision, no code yet
**Origin:** Clicky (farzaa/clicky) eval — borrow its cursor-points-at-UI teaching modality.

---

## DECISION (2026-06-18, post-/autoplan)

**Chosen: voice-first reframe.** Phase 1 is the butler's SPOKEN spatial answer
("Send is top-right, sir — the blue one") + Option C as a quiet confirmation thumbnail.
**Cut** Option B (real-cursor hijack). **Defer** the Phase 2 native overlay until demand is
proven. The factual errors below (no `Resolution.frame`, no move-cursor primitive) are
corrected in the GSTACK REVIEW REPORT at the bottom and govern the real Phase-1 work list.

The original scoping (below) is kept for context; the REVIEW REPORT supersedes it where they conflict.

## TL;DR

The question was *"Is point-and-teach a new Universal Control stage (UC7) or a separate tutor mode?"*
The code answers it: **it's a tutor mode, not a new UC stage.** UC3 already resolves a
natural-language target to a global-screen `frame: [x,y,w,h]`. Point-and-teach reuses that
resolution verbatim and only changes the *terminal action* — point + explain instead of click.

The **real** open decision is not UC7-vs-mode. It's **what surface renders the pointer**, because
VALET's frontend physically cannot draw on the macOS desktop. That's the only hard part.

---

## What already exists (no work needed)

| Capability | Where | Note |
|---|---|---|
| NL target → concrete element | `target_resolver.py:33–150` (UC3) | Returns `Resolution{ref, point, frame, label, via}` |
| Element on-screen rectangle | `action_executor.py:101–125` — `UIElement.frame = [x,y,w,h]` | **Global** coords, multi-monitor safe |
| Frame extraction | `accessibility_executor.py:188–201` | Quartz AXValue → CGPoint/CGSize |
| Screen context (shot + AX) | `perception.py:241–261` (UC2) | Already fused for the model |
| Backend→frontend push | `process_events.py:66–178` + WS broadcast `server.py:5412–5512` | Can carry `{x,y,label,frame}` today |
| Move the **real** macOS cursor | Quartz `CGEventCreateMouseEvent` (used by AX click path) | We can drive the system pointer already |

**So:** "where is the thing on screen" is solved. Resolution → `frame` → center point is a few lines.

## The one blocker

VALET's UI is a **webview (Tauri / Chrome-dev) app, viewport-locked.** It cannot draw an arrow or
glow over *other* apps' windows. Clicky achieves its effect with a native transparent, click-through,
always-on-top **NSPanel**. VALET has no equivalent: single Tauri window (`src-tauri/tauri.conf.json:10–22`),
no overlay code (`src-tauri/src/main.rs`). Pointing on the live desktop therefore needs a **new native
overlay surface** — that, and only that, is the cost.

---

## The actual decision: rendering surface

| Option | Fidelity | New native code? | Effort | What the user sees |
|---|---|---|---|---|
| **A. Native overlay** (Tauri frameless transparent always-on-top window, or Swift sidecar) | Full Clicky parity: arrow + glow + label flying across all apps | **Yes** (the hard part) | High | Pointer flies to the element in *any* app |
| **B. Real-cursor animation** | Low: just the system cursor moves to the element; spoken explanation carries the teaching | **No** — reuses existing `CGEvent` | Low | The actual mouse glides to the target while VALET narrates |
| **C. In-app highlight** | Medium-in-VALET, zero-on-desktop: snapshot the target region into the process panel with an arrow/glow | **No** — pure frontend | Low–Med | A captured thumbnail of the spot, annotated, inside VALET's window |

Option B is the sleeper: it needs **no new rendering** at all — we already move the real cursor for
clicks, so animating it to `frame`-center + narrating is almost free, and uniquely "real" (Clicky draws
a *fake* cursor; VALET would move the *actual* one).

---

## Recommendation

1. **Model it as a tutor mode, not UC7.** Add an `explain`/`point` branch where UC3 currently
   executes the click (`server.py:7165–7225`, `_resolve_and_act()`). Same observe→resolve; new terminal verb.
   Detect intent in `detect_action_fast` ("show me where…", "point at…", "where's the…").
2. **Phase the surface — ship value before building native:**
   - **Phase 1 (days):** Option B + C together. Move the real cursor to the target, emit a new
     `pointer_highlight` process-event carrying `{x,y,frame,label}`, and render the in-app annotated
     snapshot. Fully reuses UC3 + the event bus. Proves the teaching loop end-to-end.
   - **Phase 2 (later, if Phase 1 lands):** Option A — native transparent overlay for true cross-app
     pointing. Decide Tauri-frameless-window vs Swift sidecar at that point (separate spike).
3. **Don't gate Phase 1 on the native overlay.** The overlay is the expensive 20% delivering the last
   fidelity; the resolution + narration is the 80% that makes it useful.

---

## New work, concretely (Phase 1)

- `process_events.py`: add `EventType.POINTER_HIGHLIGHT` + an `emit_pointer(task_id, x, y, frame, label)` helper.
- `server.py` `_resolve_and_act()`: when intent == explain, skip click; compute `frame` center, move
  cursor (existing CGEvent path), emit pointer event, speak the explanation.
- `detect_action_fast`: route "point at / show me where / where is X" → `ui_action: "explain"`.
- `frontend/src/main.ts` (`~290–350`): handle `pointer_highlight` events.
- `frontend/src/processPanel.ts`: render the annotated target snapshot (arrow/glow on the captured region).
- Explanation text: one extra Claude call (or reuse the resolution model turn) to produce the 1–2 sentence
  "this is the X, it does Y" in VALET's butler voice.

## Open questions / risks

- **Screen Recording perm** already handled in `perception.py` for the snapshot — confirm it's granted before Phase-1 highlight.
- **Real-cursor hijack (Option B)** moves the user's actual pointer — needs a clear start/stop and barge-in
  cancel (UC5 `server.py:5540–5546` already exists; wire pointer mode into it).
- **Phase 2 native overlay** is a genuine spike: transparent + click-through + always-on-top + multi-monitor
  in Tauri is non-trivial; a small Swift sidecar may be faster and is closer to how Clicky does it.
- **Merge policy:** all of this is backend Python + native/Tauri = always-escalate surfaces. Human-reviewed PRs.

## Not doing / explicitly out of scope

- Rebuilding Clicky's STT/TTS stack (AssemblyAI/ElevenLabs) — VALET keeps Web Speech + Fish Audio.
- A second always-on transparent app — Phase 1 stays inside VALET + the real cursor.

---

## GSTACK REVIEW REPORT (/autoplan — 2026-06-18, [subagent-only], codex unavailable)

Three independent reviewers (CEO/strategy, Eng/architecture, Design/UX) read this doc cold.
No prior context shared between them. Convergence below is therefore high-confidence.

### Cross-phase themes (flagged independently by 2+ reviewers — treat as signal)

1. **Phase 1 (B+C) is a half-feature that poisons the well.** CEO Finding 5 AND Design
   Finding 8, reached independently. The magic moment (real-screen pointing) is the
   *deferred* Option A; Phase 1 ships a cursor-hijack (B, mild violation) + a look-at-a-
   picture-of-over-there panel (C, disorienting). Bad first impression → user never asks again.
2. **Voice should be primary, pointing is support.** Design Finding 7 AND CEO's reframe.
   VALET is a voice-first butler — the resilient, on-brand core is a *spoken spatial answer*
   ("Send is top-right, the blue one, sir"), which works even when every visual surface fails
   (bad permission, multi-monitor, miss). The plan buries it as "one extra Claude call."

### CRITICAL factual corrections (Eng review, verified against code — the plan is WRONG here)

- **C1 — UC3 does NOT resolve to a `frame`.** The real `Resolution` dataclass
  (`target_resolver.py:33–50`) is `{status, ref, point, label, alternatives, via, message}`
  — **no `frame` field**. The AX path returns only a `ref` (no coordinates); the vision path
  returns only a `point` (no box). The element `frame` lives on `UIElement` in the observation
  list and the executor's private `_ref_map`, NOT on the Resolution. So "Resolution → frame →
  center is a few lines" (this doc's line ~31) is false. **New plumbing required:** add `frame`
  to `Resolution` (populated in `_ax_pick`) or add `AccessibilityExecutor.frame_for_ref(ref)`.
- **C2 — ref-vs-point geometry asymmetry is unhandled.** Primary path (AX `ref`) has no
  coordinates; fallback (vision `point`) has a point but no box. Option C (glow a box) needs a
  box the common path lacks. Needs a `point_and_box(res, obs, executor)` normalizer.
- **H1 — the "move the real cursor" primitive does NOT exist.** Repo has only a *click*
  (`_mouse_click`, mouse-down/up at a point, `accessibility_executor.py:297–305`). No
  `CGWarpMouseCursorPosition`, no `kCGEventMouseMoved` anywhere. Option B is net-new code,
  not "almost free," and "glides" is wrong unless we hand-tween.
- **A1 — pointing must be structurally input-free.** `_resolve_and_act` always ends in
  `click_element`. The explain branch MUST NOT click/focus/keystroke. Prefer a dedicated
  `_resolve_and_point()` over an `intent` flag, plus a test asserting zero synthetic input.
- **A2 — barge-in won't unwind a cursor warp.** UC5 cancels the coroutine but won't move the
  pointer back; needs try/finally restore-to-origin.
- **A3/H3 — undocumented: split-permission failure (Screen-Recording-off silently kills
  Option C), and single-flight concurrency over the shared `_ref_map` / `_uc_task` slot.**
- One wrong citation: backend broadcast is `process_events.py:88–103`, not `server.py:5412–5512`.

  Net: Phase 1 is real engineering (new accessor + geometry normalizer + move primitive +
  input-free path + cursor-restore + degraded-permission + a new explain call), not "days,
  branches one line." Still smaller than the native overlay. The plan must stop calling the
  geometry "solved."

### STRATEGIC challenge (CEO review — surfaced external context this doc lacked)

VALET is a **live paid product with Stripe payouts PAUSED, UC1–3 not in any signed build,
fair-use unenforced.** The flagship "do it for you" capability has never reached a paying
customer's machine. Against that backdrop, point-and-teach is: (a) **identity-incoherent**
(VALET *does* things; teaching *withholds* the action so you do it yourself), (b) **premise
unvalidated** (no evidence users want pointing-over-doing; the idea came from a competitor eval
where we ourselves concluded the competitor does *less* than VALET), (c) **chasing a frozen
demo** (Clicky's OSS is frozen; its flying-cursor is marketing flash, not retention).
CEO verdict: **DEFER + reframe.** The only identity-coherent form is "VALET points at what it's
*about to click*, then does it" — folded into the UC confirm beat, after UC ships. Cut Phase 2.

### Design completeness: 4/10
Strong engineering reality, weak UX: ranks surfaces by effort not by the gaze-redirect feeling;
defers the only magical surface; under-designs the voice that should be the spine; omits the
five interstitial states that ARE the main path (resolving / ambiguous / miss / permission-denied
/ multi-monitor — with ambiguity resolved by voice, the butler's natural move).

### Auto-decided (6 principles)
- Correct the factual errors in this doc → **mechanical, auto-applied** (code is ground truth).
- Cut Phase 2 native overlay from active scope → **P2/P3**, deferred to TODO pending demand proof.
- Lead with voice narration as the deliverable → aligns P1 (completeness of the resilient path) + P5.

### Decision for the human (NOT auto-decided — premise + user challenge)
Both independent voices recommend changing the stated direction. The user's original direction
(build Phase 1 = B+C now) stands unless explicitly changed. See the final gate.
