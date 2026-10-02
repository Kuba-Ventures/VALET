# VALET Code-Quality Audit — Findings & Removal Plan

*Snapshot: 2026-07-20. A reachability + duplication audit of the whole repo. This is a
planning document, not an executed change — it deletes nothing. Line counts and file
references are point-in-time; re-grep before acting on any individual item.*

**Method:** the repo was partitioned into 9 non-overlapping clusters and audited in
parallel. "Alive" = reachable from the two real entrypoints
(`src-tauri/src/main.rs` → `server.py`, including lazy/in-function imports) or shipped by
`packaging/`. Every DEAD/DUPLICATE claim below is grep-backed. Aggressiveness: moderate
(flag provably-unreferenced files, backups, and genuine duplicates; leave clearly-WIP
wired code alone).

**Big picture:** the codebase is largely alive and reasonably factored. The apparent
"grab-bag" (5 executors, 3 calendar modules, multiple panels/input layers) is mostly
*intentional layering*, not copy-paste drift. The real waste is concentrated in
**abandoned-but-never-deleted files**: superseded prototypes, a pre-rename ("Jarvis")
overlay, and an orphaned pre-redesign component tree in the marketing site.

Two of the biggest-looking targets are NOT clean deletes and are called out as
**decisions/bugs**, not cleanup.

---

## TIER 1 — Dead files, zero references, safe to delete (~2,900 LOC + binaries)

| Item | LOC | Why dead | Risk |
|---|---|---|---|
| `conversation.py` | 252 | zero imports anywhere; abandoned parallel of `planner.py`'s planning-session types | low |
| `monitor.py` | 174 | standalone dev CLI log-tailer; not imported, not packaged | low |
| `evolution.py` | 268 | zero refs anywhere incl. tests | low |
| `global_ptt.py` + `tests/test_global_ptt_chord.py` | 169+128 | legacy CGEventTap; `server.py:5285/10157` comments say retired — chord tap now in Rust `main.rs` | low |
| `browser.py` + `tests/test_browser_integration.py` | 336+ | Playwright browser; superseded by native `web_search`/`web_fetch` (RESEARCH) & `actions.open_browser` (BROWSE). **Also drop `playwright>=1.40.0` from requirements.txt** (sole user) | low-med |
| `helpers/` (whole dir) | ~371 + 78KB bin | 4 historical calendar-fetch prototypes (.py/.sh/.js/.swift/binary) superseded by `apple_calendar.py`; untouched since baseline | low |
| `frontend/src/orb.backup.ts` | 199 | near-dup of `orb.ts`, zero refs (also silently costs a `tsc` pass) | low |
| `desktop-overlay/` (`ValetOverlay.swift` + `JarvisOverlay` binary) | — | superseded by `main.rs::spawn_cursor_overlay`; retired "Jarvis" name; a compiled binary in git | low |
| `src-tauri/icons/menu/*.png` (7) | — | replaced by inline SVG in `tray-menu.html` since #158 | low |
| `.jarvis/` (whole dir) | — | pre-rename artifact; live `ship_via_file` writes to `<project>/.valet/inbox/` | low |
| product-site: 11 orphaned components* | ~575 | pre-redesign tree, all 0 importers, superseded by `components/home/*` (PR #141) | low |

\* `Hero, Orb, DemoTerminal, Pricing, HowItWorks, Capabilities, Sequence, Waveform,
Constellation, ActionStack, Faq` (`.tsx`). Delete in dependency order (Sequence + its 3
visuals together; Hero + Orb together). `Faq.tsx` is a drifted dup of live `FaqAccordion.tsx`.

**Dependency chains to respect:**
- If `evolution.py` **and** `ab_testing.py` (Tier 4) both go → `templates/prompts/*.yaml`
  becomes dead too (they're its only readers; `templates.py` is separate & alive).
- `browser.py` deletion → also remove the Playwright dep + any `playwright install` CI step.

---

## TIER 2 — Dead code inside living files (unused exports/methods, ~450 LOC)

Low-risk trims once a final grep confirms no dynamic caller. Grouped by file:

- **calendar_access.py** (~140): `get_upcoming_events, get_next_event, format_schedule_summary,
  refresh_cache, create_event, get_events_for_date` — imported into `server.py` but never called.
  ⚠️ May be an *unfinished* Google create/delete feature — confirm intent before deleting.
- **mail_access.py** (~90): `get_accounts, get_messages_from_account` (not even imported),
  `search_mail, read_message, format_messages_for_context, get_recent_messages`.
  ⚠️ `search_mail`/`read_message` look like future voice-search hooks — confirm intent.
- **memory.py** (~50): `get_recent_memories(547), get_notes_by_topic(695), format_plan_for_voice(764)`
  — unused siblings of live functions.
- **notes_access.py**: `get_recent_notes, search_notes_apple` — imported, never called.
- **work_mode.py**: `restore(440), _save_session(421), _clear_session(433)`.
  ⚠️ Looks like a half-shipped "resume session" feature — check recent PRs before deleting.
- **planner.py**: `start_planning(399), get_working_dir(666)`.
- **plan_stages.py**: `advance_to(181)` (dead public wrapper; private `_advance_to` is the live one).
- **project_context.py**: `stop_all_watchers(411)` (only singular `stop_watcher` is used).
- **frontend/src/settings.ts**: `isSettingsOpen(1045)` unused export.

---

## TIER 3 — Real duplication to consolidate (structural; test after)

| Dup | Action | Risk |
|---|---|---|
| Window/PID helpers copied in `accessibility_executor.py:133-192` & `perception.py:75-127` (already drifted) | extract shared `window_focus.py`, import from both | low (both have tests) |
| `google_calendar.py` re-implements the same Google Calendar query as `calendar_access.py::_list_events_blocking` (different output shapes, for the Apple+Google merge) | have `google_calendar.read_events()` reshape `calendar_access` output instead of re-querying | **moderate** (two live paths behave differently) |
| `docs/research_routing_diagnosis.md` + `_v2` + `_v3` | keep `_v3`, drop v1/v2 | low |

---

## TIER 4 — Decisions & bugs to route to a human (NOT clean cleanup)

1. **`qa.py` + `suggestions.py` (~420 LOC) are dead *because of a bug*, not by intent.**
   `task_manager.py:204-253` uses `qa_agent`/`suggest_followup` **without importing** them →
   `NameError` on every `_run_qa()`, swallowed by a bare `except` at `:260`. The auto-QA /
   proactive-suggestion feature is **silently broken in production.**
   → Decide: *fix the imports* (feature was wanted) or *delete all three* (feature abandoned).
2. **`ab_testing.py` (289)** — test-only (2 test files). If retired, also retires
   `templates/prompts/` (see chain above) and edits those 2 tests.
3. **`learning.py` (193)** — zero imports; `tracking.py`'s docstring references it but never
   imports it. Meant-to-be-wired, or abandoned?
4. **`tools/stt_ab_review.py`** — standalone, unimported CLI. Likely intentional dev tooling; confirm.
5. **`sttCompare.ts`** — "alive" but self-described as temporary A/B instrumentation (#320/#321).
   Retire if the experiment concluded.
6. **Doc hygiene** (not code): `coding-plans/*.md`, several closed-bug `docs/*_diagnosis.md`,
   `docs/stt_provider_investigation.md` (superseded by shipped Deepgram); consider splitting the
   228KB `PROJECT.md` into current + `docs/project_history.md`.

---

## KEEP — do not misclassify as dead

- **`self_mod.py`** — dev-only, deliberately excluded from ship builds (`valet.spec` excludes),
  but alive via `_load_self_mod()` + 10 call sites.
- **launchd scripts** (`scripts/*launchd*`, `restart.sh`, `smoke_test.sh`, `start.sh`,
  `ax_smoke.py`, `voice_latency_baseline.py`) — a separate *dev/self-mod* run path, load-bearing
  for that workflow. Recommend a one-line comment/doc noting it's intentionally separate from the
  Tauri ship path so nobody "cleans it up."
- The **5 executors**, the **planner/plan_stages/task_manager** trio, **project_context vs
  project_scanner**, **agents.py vs agent_loop.py**, the frontend **panel/input layers** — all
  intentional separation, not duplication.
- All product-site `lib/`, `supabase/`, Stripe/license code — alive; **human-review** by policy.

---

## Bugs / correctness issues found in passing (fix independent of cleanup)

1. **Packaging:** `google_calendar.py` is lazily imported (`server.py:7403`) but missing from
   `valet.spec` `hiddenimports` → frozen build may silently drop it and break the calendar merge.
2. **`task_manager.py` missing imports** (Tier-4 #1) — a real swallowed `NameError`.
3. **CI gates only 2 of 31 test files** (`factory.yml` runs `test_e2e_pipeline` +
   `test_feedback_loop`). "Tests pass" is weak assurance for these removals — verify manually.
4. **Stale `CLAUDE.md`:** describes `mail_access.py` as "Apple Mail (READ-ONLY)"; it's now
   Gmail-API with draft *write* (`create_draft`). Update the doc.

---

## Suggested execution order

1. **Tier 1** as one PR (pure dead files) — biggest win, lowest risk. Run the app +
   `tsc`/`cargo check` after (CI won't catch much — see bug #3).
2. **Fix the `valet.spec` google_calendar gap** (bug #1) — small, independent, prevents a
   shipped regression.
3. **Decide Tier-4 #1** (qa/suggestions: fix vs delete) before touching those files.
4. **Tier 2** (dead-inside-alive) — after confirming the two ⚠️ "unfinished feature" cases.
5. **Tier 3** consolidations — one at a time, with the existing unit tests, since these are behavioral.
6. **Doc hygiene** whenever convenient.
