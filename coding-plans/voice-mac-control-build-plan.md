# Build Plan: Voice-native Mac control (console + visible cursor + speed)

Staged execution of the approved design (`~/.gstack/projects/.../finley-feat-point-and-teach-design-*.md`,
Approach A). Each stage is its own branch + PR (backend Python = human-reviewed per merge policy),
ships independently, has unit tests + a live smoke test + a demo artifact, and a hard "done when" gate.
Order = value/risk: measure first, then the high-value low-risk console, then the risky cursor.

Targets (locked): console launch < ~800ms (speech-end → app visible); voice→cursor→click < ~1.5s.

---

## STATUS (2026-06-19) — shipped vs remaining

### ✅ Shipped to main
- **Stage 0 — latency harness** (#114): per-turn timing + `GET /api/latency/last`.
- **Stage 1 — voice console** (#116): "open X" launches installed apps with no LLM round-trip;
  fuzzy match incl. nested `.app` bundles + space-insensitive ("da vinci" → DaVinci Resolve).
- **Stage 2 — visible cursor glide + hit-test-guarded click** (#117): cursor glides to the target,
  AX hit-test verifies before clicking, aborts on mouse-grab; in-app takeover banner (#119).
- **Point-and-teach (voice-only)** (#113): "where is X" speaks the on-screen location.
- **Safety/UX:** Escape hard-stop kills voice + sleeps (#118); no confirm panel + faster miss,
  ~5s→~1-2s (#120); echo-loop fix — Vee stops hearing itself (#121).
- **PTT-primary** (#122): hold ⌥ to talk (default), toggle for always-listening — kills wake latency.

### 🔭 Remaining roadmap (build-ready, ordered)

**CLICKY track (visual Mac control)**
- **Stage 3 — Native cursor follower** (the on-desktop blue custom dot, the real Clicky look).
  - *Build:* a native transparent, always-on-top, **click-through** overlay that tracks the real
    cursor and draws a custom shape/color dot. Option: Tauri frameless window, OR a small Swift
    sidecar launched by `src-tauri`. Poll `CGEventGetLocation` (or hook) for position; draw at it.
  - *Why it's gated:* VALET's webview can't draw over other apps — this is THE native-overlay spike.
    macOS-version-fragile. **`/plan-eng-review` first** (Tauri-window vs Swift-sidecar).
  - *Bonus:* the same overlay powers point-and-teach's on-desktop pointing (3b below).
  - *Bundles with:* Global hold-⌥ PTT (same native layer — one investment, two features).
  - *Effort:* native spike (~3-5 days). *Demo:* the blue dot flying to a Gmail button.
- **Stage 3b — point-and-teach visual** (once the overlay exists): `_resolve_and_point` draws the
  dot + a label at the located element, instead of voice-only. Reuses `emit_pointer` (already built).
- **Stage 3c — parallel narration polish:** butler narrates WHILE the cursor glides so latency hides
  behind motion (the old "Approach C" perceived-speed idea). Cheap once 3 lands.

**RAYCAST track (fast voice console)**
- **Stage 4 — Console v2** (expand beyond installed apps). Each is a fast-path in `detect_action_fast`
  + a handler, all timed via Stage 0:
  - Deep links / URLs ("open github.com"), system actions (lock, sleep, volume, brightness, dnd),
    snippets / text expansion, inline calculator ("what's 15% of 80"), clipboard history,
    window management ("snap left"), quick file search.
  - *Effort:* incremental, low-risk; pick the 3-4 highest-value first (system actions + calculator
    + deep links likely). MVP was installed-apps-only on purpose.

**WISPR / native track (activation)**
- **Stage 5 — Global hold-⌥ PTT** (works when VALET ISN'T focused — true Wispr). Native global
  shortcut: Tauri `globalShortcut` registering ⌥, or the Swift sidecar. **Bundles with Stage 3**
  (same native layer). `/plan-eng-review` together. Today's PTT is window-focused only.

**CROSS-CUTTING / must-not-forget**
- **DMG rebuild + release:** all merged work is **dev-server-only** until a signed/notarized build
  ships. The `/Applications` app (and customers) get none of it until then. Wire/verify the release
  pipeline before the Jacques demo "ships."
- **Branch protection:** enable "require branches up to date before merging" — TWO squash-merges
  silently dropped commits this session (#114 dropped #113's server code; #116's nested-bundle fix
  dropped). The factory's frontend `npm run build` gate already catches TS errors; this closes the
  stale-branch hole.

---

## Stage 0 — Latency harness ("extremely fast" becomes a number)
**Why first:** every later stage is graded against this. Also directly answers Jacques's mandate and
produces the baseline clip. Lowest risk.

- **Build:** timing markers through the voice turn — speech-end → intent-detected → action-start →
  first-result (app visible / click fired) → speech-start. Log per-turn ms with a turn id. Add a
  `/api/latency/last` (or structured log line) so a clip can show real numbers.
  - Files: `server.py` (ws/voice handler + `_resolve_and_act` / dispatch), TTS path.
- **Test:**
  - Unit: timer/aggregation math (`tests/test_latency.py`), no network.
  - Live: run "open Safari" and "click the X", read logged ms.
- **Done when:** every voice turn logs a phase breakdown; baseline numbers for "open X" and "click Y"
  captured on the dev machine.
- **Demo artifact:** the 60-second baseline clip (stopwatch + logged ms).

## Stage 1 — Raycast-style voice console (the headline)
**Why second:** highest value, low risk, reuses the existing launcher. This is what wins the speed room.

- **Build:** enumerate installed apps once at startup (cache); fuzzy-match the spoken target
  ("the browser" → Safari) ; launch via the existing app-launch path; speak the ack in parallel with
  the launch so perceived latency is just the launch. MVP scope = **installed apps only** (deep links /
  system actions / snippets deferred). Honest "I don't see an app called X, sir" on no match.
  - Files: `actions.py` (`open_app_or_path` / app_launch), `server.py` `detect_action_fast` ("open X"),
    a small app-index module + fuzzy matcher.
- **Test:**
  - Unit: fuzzy matcher against a fixture app list (`tests/test_console_match.py`) — aliases, casing,
    near-misses, no-match.
  - Live: "open <app>" meets the < ~800ms target measured by Stage 0.
- **Done when:** "open almost any installed app" works by voice and hits the launch target.
- **Demo artifact:** clip of a rapid string of instant launches.

## Stage 2 — `move_cursor` primitive + visible cursor click (the Clicky look)
**Why third / isolated:** the one genuinely risky piece (frame-smooth tween from Python; fighting the
user's physical mouse). Spike before committing — run `/plan-eng-review` on this stage only.

- **Build:** new `move_cursor(x, y)` in `accessibility_executor.py` using **tweened `CGEventMouseMoved`**
  (NOT warp), frame/duration-budgeted. Wire into the UC act path so "click the X" moves the cursor
  visibly, then clicks.
  - Files: `accessibility_executor.py` (next to `_mouse_click` ~297–305), `server.py` `_resolve_and_act`
    click branch.

- **Architecture (locked by /plan-eng-review 2026-06-18):**
  - **Tween runs on a dedicated worker thread** (`run_in_executor`), NOT the asyncio voice loop —
    a busy loop must not jank the motion. `CGEventPost` is thread-safe. The thread posts
    `kCGEventMouseMoved` on a steady ~16–30ms timer over a duration budget, with eased waypoints.
  - **Abort = position-divergence check.** Each frame, compare the actual cursor location
    (`CGEventGetLocation`/`NSEvent.mouseLocation`) to our last-commanded point; if it diverges past a
    threshold the user grabbed the mouse → abort immediately and yield. No CGEventTap/run-loop.
  - **Click guard = AX hit-test at landing.** Before clicking, `AXUIElementCopyElementAtPosition(x,y)`
    and click ONLY if it matches the resolved element (ref/role/title); else abort + say so honestly.
    No second full resolve (keeps it fast). Thin/Electron match → abort (safe default).
  - **Coordinate space:** CGEvent mouse coords are global display **points**, same space as
    `UIElement.frame` — no Retina pixel conversion. Multi-monitor works because points are global.
  - **Reuse:** UC3 resolution `frame` (point-and-teach), `_mouse_click` for the terminal click,
    Stage 0 `_timed_action` for latency, UC5 `_track_uc` slot for barge-in cancel.
  - **Boundary:** the locate/point path (`_resolve_and_point`) stays structurally input-free — the
    existing source-guard test must stay green.

- **Test (100% of tween/abort/guard logic is unit-testable via injection):**
  - Inject the clock, the cursor-position reader, and the event-poster into `move_cursor` so motion is
    deterministic headless. `tests/test_move_cursor.py`:
    - tween waypoint generation (fake clock → N eased points)
    - position-divergence abort (inject a cursor jump → aborts, no further posts)
    - per-move timeout (stalled clock → aborts)
    - arrival within tolerance
  - Click branch: hit-test MATCH → exactly one click; **hit-test MISMATCH → abort, ZERO clicks
    (CRITICAL safety test)**; barge-in mid-move → stops, no click; multi-monitor global-point coords.
  - Regression: point-and-teach input-free source-guard stays green.
  - Manual (on device): "click the X" glide+click < ~1.5s; grab mouse mid-glide → yields instantly.
- **Done when:** voice→cursor-glides→click works, feels instant, aborts on mouse-grab, and never clicks
  a mismatched element.
- **Demo artifact:** the "watch it work" clip — cursor gliding to a Gmail/web button and clicking.

## Stage 3 — Parallel narration + perceived-speed polish (post-Jacques, optional)
Butler narrates while the cursor moves so latency hides behind motion (Approach C territory). Defer until
after the sponsor demo; only worth it once Stages 0–2 are solid.

---

## Cross-cutting
- **Branch/PR per stage** off `main`; each is backend Python → human review (not auto-merge).
- **Every stage reports against Stage 0's budget** — no stage ships if it regresses the latency number.
- **No stage touches** billing/account/admin (the deprioritized surfaces) — stay in the agent path.
- **Sequencing:** 0 → 1 can proceed immediately (low risk). 2 gated behind a `/plan-eng-review` spike
  on the tween. 3 deferred.

---

## Stage 2 eng-review (archived 2026-06-18)

Eng review of **Stage 2** (`move_cursor` + visible click), 2026-06-18, branch `feat/voice-console`.

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_resolved | 3 architecture decisions locked, 0 critical gaps |

### Decisions locked (the 3 that carry the risk)
1. **Tween runtime → dedicated worker thread** (not the asyncio voice loop). Isolates frame-smoothness
   from a busy loop, which is the stated demo risk. CGEventPost is thread-safe.
2. **Abort seam → per-frame position-divergence check** (not a CGEventTap). Compares actual cursor
   location to last-commanded; diverge → user grabbed it → yield instantly. Cheap, no run-loop.
3. **Click guard → AX hit-test at landing** (not full re-resolve, not blind-trust).
   `AXUIElementCopyElementAtPosition` must match the resolved element or the click aborts. Safe without
   a second resolve round-trip; thin/Electron match → abort (safe default).

### What already exists (reused, not rebuilt)
- UC3 resolution now carries `frame` (point-and-teach #113) — the on-screen target rect.
- `_mouse_click` (CGEvent down/up) — the terminal click.
- Stage 0 `_timed_action` — latency measured against the budget.
- UC5 `_track_uc` slot — barge-in cancels the move mid-glide.
- Coordinate space already aligned: CGEvent points == AX frame points (no Retina conversion).

### NOT in scope (deferred, with reason)
- **Native overlay / drawn cursor** — the real cursor is the mechanism; no Tauri/Swift overlay.
- **CGEventTap abort** — heavier; position-divergence covers the real case.
- **Full re-resolve before click** — too slow for the speed mandate; hit-test verify instead.
- **Non-left-click / drag / scroll** — only a left-click after the glide.

### Failure modes (each has a test + safe behavior)
- Loop busy → motion jank → **mitigated** by the worker thread; manual smoothness check on device.
- User grabs mouse mid-glide → **abort** (position-divergence test); user keeps control.
- UI shifts during glide → **hit-test mismatch → zero clicks** (CRITICAL unit test); honest message.
- Move never reaches target → **per-move timeout abort** (test). No silent hang.
- Barge-in mid-move → **cancel via `_track_uc`** (test); cursor stops, no click.
- No critical gaps: every failure mode is either tested + handled, or a flagged manual-on-device check.

### Test plan
Tween/abort/guard logic is 100% unit-testable by injecting the clock, cursor-position reader, and
event-poster into `move_cursor`. On-screen smoothness and the mouse-grab feel are manual-on-device
(can't unit-test perceived motion). The CRITICAL test is hit-test-MISMATCH → no click.

### Outside voice
Skipped — Codex not installed; decisions are grounded in the actual CGEvent/AX code and the prior
3-reviewer pass on the design doc.

**UNRESOLVED:** none — all 3 decisions answered.
**VERDICT:** ENG CLEARED — architecture locked, ready to implement Stage 2.

---

## NATIVE STAGE — locked architecture (eng-review 2026-06-19)

The on-desktop colored cursor follower + global hold-to-talk. ONE native-overlay
spike. Tauri v2; production webview navigates to the `valet-backend` sidecar at
`localhost:8340` (main.rs); single "main" window today.

### Decisions locked (the 2 that carry the risk)
1. **Rendering → fullscreen transparent click-through overlay + CSS-transform dot**
   (NOT per-frame window moves, which stutter; NOT a Swift sidecar). One always-on-top
   click-through window per display; Rust polls the cursor and emits coords; the overlay
   JS moves the dot via CSS transform (GPU-smooth). The dot is CSS — recolor/reshape free.
   - Click-through: `set_ignore_cursor_events(true)` on the overlay window.
   - Coord space: cursor location is global **points**; map to overlay-local per display,
     Retina-safe (points, not pixels) — same point space as `UIElement.frame`.
2. **Global PTT → ⌥-Space chord** via `tauri-plugin-global-shortcut` (add dep). Bare ⌥
   alone is NOT registerable as a global accelerator — confirmed feasibility gotcha. The
   plugin reports **Pressed/Released**, so hold-to-talk works; Pressed→`beginPushToTalk`,
   Released→`endPushToTalk`. The in-window bare-⌥ (#122) still works when VALET is focused.

### What already exists (reused, not rebuilt)
- `main.rs` sidecar/window plumbing — add the overlay window in `setup()`.
- `emit_pointer` (#113) + the glide path (#117) — the dot consumes these to appear during
  point-and-teach and glide-and-click. Reuse `cursor_control` events (#119) for show/hide.
- The PTT handlers (`beginPushToTalk`/`endPushToTalk`, #122) — the global chord feeds them.

### NOT in scope (deferred, with reason)
- **Bare-⌥ global** — not registerable; would need a permissioned CGEventTap (heavier, fragile).
- **Swift sidecar** — fullscreen-overlay + CSS gets the same visual in-stack.
- **Per-display dynamic add/remove** beyond launch-time displays — handle current displays first.

### Failure modes
- Overlay blocks clicks (click-through misconfigured) → **CRITICAL manual check** before merge.
- Cursor-follow stutter → mitigated by CSS-transform (no window moves); **manual smoothness check**.
- Multi-monitor / Retina mis-mapping → **unit test the coord mapping** + manual on real HW.
- ⌥-Space collides with an app's shortcut → make the chord configurable; document.
- macOS-version fragility (overlay/global-shortcut APIs) → **manual on the target macOS**.
- Critical gap to watch: **click-through** — if wrong, the overlay eats every click. Gate merge on it.

### Test plan
- **Unit:** coord mapping (global point → overlay-local; single + multi-monitor + Retina) and the
  dot show/hide-on-event logic. Pure functions / event handlers.
- **Manual-on-device (can't unit-test):** smoothness, click-through, ⌥-Space Pressed/Released → PTT,
  multi-monitor coverage. **This stage can ONLY be exercised in the Tauri app (`tauri dev` / a build),
  NOT the vite dev server** — a real workflow shift from every prior stage.

### Outside voice
Skipped — Codex not installed; decisions grounded in the actual `main.rs` + Tauri v2 facts.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 (native stage) | clean | 2 architecture decisions locked, 1 critical-gap flagged (click-through), 0 unresolved |

- **UNRESOLVED:** none — both decisions answered (rendering = fullscreen overlay + CSS dot; global PTT = ⌥-Space chord).
- **VERDICT:** ENG CLEARED — native-stage architecture locked. Build gated on the manual click-through + smoothness checks in `tauri dev` before merge. (Stage 2 eng-review archived above.)
