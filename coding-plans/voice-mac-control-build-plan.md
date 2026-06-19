# Build Plan: Voice-native Mac control (console + visible cursor + speed)

Staged execution of the approved design (`~/.gstack/projects/.../finley-feat-point-and-teach-design-*.md`,
Approach A). Each stage is its own branch + PR (backend Python = human-reviewed per merge policy),
ships independently, has unit tests + a live smoke test + a demo artifact, and a hard "done when" gate.
Order = value/risk: measure first, then the high-value low-risk console, then the risky cursor.

Targets (locked): console launch < ~800ms (speech-end → app visible); voice→cursor→click < ~1.5s.

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
  (NOT warp — a teleport has no visible travel and kills the effect), frame/duration-budgeted. Required
  guards: **abort on physical mouse input**, **per-move timeout**, **re-resolve target before the click
  fires**. Wire into the UC act path so "click the X" moves the cursor visibly, then clicks.
  - Files: `accessibility_executor.py` (next to `_mouse_click` ~297–305), `server.py` `_resolve_and_act`
    click branch.
- **Test:**
  - Unit: tween waypoint generation + abort logic with the event-post mocked (`tests/test_move_cursor.py`).
  - Regression: the point-and-teach input-free guarantee test stays green (locate path still never clicks).
  - Live: "click the X" shows cursor travel + click under < ~1.5s; grabbing the physical mouse aborts it;
    a mis-resolve never clicks.
- **Done when:** voice→cursor-flies→click works, feels instant, and aborts safely.
- **Demo artifact:** the "watch it work" clip — cursor flying to a Gmail/web button and clicking.

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
