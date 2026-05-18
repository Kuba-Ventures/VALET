# Design Partner — Manual Test Scenarios

Test scenarios for each phase of the Design Partner build. Populated incrementally as each phase lands. Run from a clean repo state with `scripts/start.sh` running unless noted otherwise.

## Phase 0 — Scaffold setup

> Verify `docs/design_partner_tests.md` exists (this file). Verify `config/design_partner.json` exists and is valid JSON. Verify `data/logs/` directory exists.

## Phase 1 — Project lifecycle

### 1.1 — `new project for testclient`
1. Start backend + frontend per `scripts/start.sh`.
2. Say "new project for testclient" into the orb.
3. **Verify directory:** `~/Code/testclient/` exists, contains `.git/` and `CLAUDE.md`.
4. **Verify CLAUDE.md scaffold:** opens with `# testclient` header, has Client / Value prop / Current MVP scope / Recent meeting notes sections, and the placeholder `<!-- TODO: JARVIS will populate from memory layer in a future phase -->`.
5. **Verify Cursor opens** to `~/Code/testclient/` with the file tree visible.
6. **Verify Terminal opens** in the same directory with `claude` waiting at its prompt (no auto-typed input).
7. **Verify side-by-side layout:** Cursor occupies left ~60% of screen, Terminal occupies right ~40% (per `config/design_partner.json` `window_layout`).
8. **Verify Process Panel** appears and shows a sequence of `project.*` events: `creating → scaffolding → opening_cursor → app_launch(Cursor) → opening_terminal → app_launch(Terminal) → ready`, all on one task.
9. **Verify alias recorded:** in a Python REPL, `from memory import list_known_projects; print(list_known_projects())` shows `testclient` mapped to `~/Code/testclient`.

### 1.2 — `open testclient`
1. With Cursor + Terminal closed from 1.1, say "open testclient".
2. **Verify no duplicate dir created** — `~/Code/testclient/` is the same one.
3. **Verify Cursor + Terminal both reopen** with the same side-by-side layout.
4. **Verify `last_opened_at` bumped:** `list_known_projects()` shows updated timestamp for `testclient`.
5. **Verify Process Panel** shows `opening → opening_cursor → opening_terminal → ready` events (NOT `creating`/`scaffolding` since dir already exists).

### 1.3 — `open <fuzzy partial name>`
1. Say "open test" (partial match).
2. **Verify** JARVIS resolves to `testclient` via fuzzy substring match and opens it.
3. Create a second project `testbeta` via "new project for testbeta".
4. Say "open test" again.
5. **Verify** JARVIS refuses with "I found 2 projects matching 'test', sir — try more of the name." Both projects' names appear in the error event detail.

### 1.4 — `list my projects`
1. Say "list my projects".
2. **Verify** JARVIS replies immediately (no LLM round-trip) with a sentence like "2 projects, sir: testclient, testbeta." Or however many are in `~/Code/` + alias table.
3. **Verify NO Process Panel task** fires (fast-action path bypasses task_context).

### 1.5 — Existing voice commands still work (don't-regress)
1. Say "what's my schedule" — verify calendar fast-action still fires.
2. Say "research the latest Anthropic blog post" — verify standard Haiku reply + `[ACTION:RESEARCH]` dispatch + Process Panel narration.
3. Say "close it" — verify Process Panel dismisses (existing `close_panel` path).
4. Say "what do I need to do" — verify `check_tasks` fires (not `list_projects` — distinct keyword set).

### 1.6 — CLAUDE.md not clobbered on reopen
1. After 1.1, manually edit `~/Code/testclient/CLAUDE.md` and add a line "Custom content here".
2. Say "open testclient" again.
3. **Verify** the custom content is preserved (the open path doesn't write CLAUDE.md, only `new_cursor_project` scaffolds it on a fresh dir).

### 1.7 — Configurable project roots
1. Confirm `config/design_partner.json` has `"project_roots": ["~/Code", "~/projects"]`.
2. In a REPL: `from actions import _project_roots; print(_project_roots())` → both paths listed (`~/projects` will be skipped at scan time if it doesn't exist; that's expected).
3. Edit `config/design_partner.json` and add `"~/Documents"` to `project_roots`. Restart server (`launchctl kickstart -k gui/$UID/com.jarvis.backend`).
4. Say "list my projects" — **verify** any git-tracked / non-hidden dirs under `~/Documents/` appear in the spoken list.
5. **Caution noted in docs:** adding broad roots like `~` surfaces every home-dir folder. Prefer narrow roots (`~/Code`, `~/projects/clients`) or per-project `register` for scattered repos.

### 1.8 — Register a project outside any root (`venture-crm`)
Pre-condition: `~/venture-crm` exists on disk (the user's real project).
1. Say "register ~/venture-crm as venture-crm".
2. **Verify** JARVIS replies "Registered 'venture-crm' at /Users/finley/venture-crm, sir."
3. **Verify** in REPL: `from memory import resolve_project; resolve_project('venture-crm')` returns `/Users/finley/venture-crm`.
4. Say "open venture-crm" — **verify** Cursor + Terminal open at `~/venture-crm` side-by-side, warm context loads.
5. Say "open venture crm" (no hyphen) — **verify** same result (resolver normalizes separators).
6. Say "list my projects" — **verify** `venture-crm` appears with `source: alias`.

### 1.9 — Register variants
1. "register /Users/finley/foo as foo" — **verify** absolute path accepted, alias=`foo`.
2. "remember ~/Downloads/old-app as my old app project" — **verify** alias=`old app` (multi-word).
3. "add this project: ~/sideproject" — **verify** alias defaults to dir basename (`sideproject`).
4. Negative: "remember to call sarah tomorrow" — **verify** does NOT match register (no path token; falls through to LLM for the existing REMEMBER tag).
5. Negative: "add a task to call sarah" — **verify** does NOT match register (no `/` or `~` in target).
6. Register a path that doesn't exist: "register ~/nonexistent as foo" — **verify** JARVIS replies "That path doesn't exist, sir: /Users/finley/nonexistent" and no DB row is written.

### 1.10 — Ambiguous-across-roots refusal
1. Create `~/Documents/venture-crm/` (empty test dir) so `venture-crm` exists in two configured roots (assumes `~/Documents` was added to `project_roots` in 1.7).
2. Say "open venture-crm".
3. **Verify** JARVIS refuses with "I found 2 projects called 'venture-crm' in different roots, sir — say which: /Users/finley/Documents/venture-crm, /Users/finley/venture-crm" (or similar).
4. **Verify** the alias-table hit still resolves cleanly to `~/venture-crm` (alias wins over fs scan).
5. Cleanup: `rmdir ~/Documents/venture-crm`.

## Phase 2 — Warm context loader

### 2.1 — Load on `open <project>`
1. With Phase 1 lifecycle wired, say "open testclient" (or any existing project under `~/Code/`).
2. **Verify** Process Panel narrates a `Loading context: testclient` task with sub-events: `context.loading → context.file_read (CLAUDE.md) → context.file_read (README.md) → context.file_read (File tree) → context.file_read (git log) → context.file_read (entry_point.py) → context.ready`.
3. **Verify** in a REPL: `import project_context; ctx = project_context.get_active(); print(ctx.summary_for_prompt()[:500])` returns a markdown block with the project name and CLAUDE.md content.

### 2.2 — Auto-load on `new project for <name>`
1. Say "new project for foo-test".
2. **Verify** lifecycle events fire (project.creating → project.ready) AND a separate `Loading context: foo-test` task fires immediately after.
3. **Verify** loaded context has `entry_points={}` (fresh dir, no Python/Node entry points), `file_tree` lists `CLAUDE.md` only (git ls-files in a fresh git repo with one commit, or fallback walk finds CLAUDE.md), `claude_md` is the scaffold from Phase 1.

### 2.3 — Filesystem watcher refresh
1. After 2.1, open `~/Code/testclient/CLAUDE.md` in Cursor and add a line. Save.
2. **Verify** within 1 second a new `Context refreshed: testclient` task appears in the Process Panel.
3. **Verify** `project_context.get_active().claude_md` now contains the edit.
4. Edit a NON-watched file (e.g., create `~/Code/testclient/unrelated.txt`).
5. **Verify** NO refresh fires (the watcher filters to CLAUDE.md / README.md / loaded entry points only).

### 2.4 — Voice "refresh context"
1. Say "refresh context" with at least one project loaded.
2. **Verify** JARVIS says "Refreshing context, sir." once (NOT a double-speak with the completion message — the fast-action path dispatches a silent background refresh).
3. **Verify** Process Panel shows a `Refreshing context: <name>` task with `context.refreshed` as the final event.
4. Say "refresh context" with NO project loaded (fresh boot, no prior open).
5. **Verify** JARVIS still says "Refreshing context, sir." and the background refresh logs a warning but does not crash.

### 2.5 — `[ACTION:REFRESH_CONTEXT] <name>` via LLM
1. With multiple projects loaded, say something the LLM might paraphrase as "reload the cerwood context".
2. **Verify** LLM emits `[ACTION:REFRESH_CONTEXT] cerwood`, JARVIS speaks "Context refreshed for cerwood, sir." after the refresh completes.
3. With `cerwood` NOT in the alias table, repeat with `[ACTION:REFRESH_CONTEXT] nonsense`.
4. **Verify** JARVIS says "I don't have 'nonsense' on file as an open project, sir." and no Process Panel task fires.

### 2.6 — `open_claude_in_project` no-clobber (regression check)
1. In a project with an existing CLAUDE.md (Phase 2 source of truth), trigger the legacy BUILD path with a prompt.
2. **Verify** the existing CLAUDE.md is untouched.
3. **Verify** a sidecar `.jarvis_prompt.md` was written with the new prompt instead.

### 2.7 — Don't-regress (Phase 1 still works)
1. Say "list my projects" → verify fast-action reply.
2. Say "new project for regression-check" → verify full Phase 1 + Phase 2 flow (lifecycle events + warm context load).
3. Say "what's my schedule" → verify calendar fast-action unaffected.

## Phase 3 — Design Panel

### 3.1 — Start a design session
1. With at least one project open (warm context loaded), say "let's design a daily rollup".
2. **Verify** Design Panel slides in from the LEFT (Process Panel docks right; they don't overlap on a wide screen).
3. **Verify** header shows: topic = "daily rollup", state badge = DESIGNING (green), close button (×).
4. **Verify** JARVIS speaks "Right, sir — let's design 'daily rollup'." or similar.

### 3.2 — Five-turn conversation
1. Have a 5-turn conversation about the rollup ("what should it summarize?", "where should it write the file?", etc.).
2. **Verify** each turn produces a voice reply (1-5 sentences, British butler tone, no "I" sentences).
3. **Verify** decisions / questions / assumptions appear in the timeline as the conversation produces them, with distinct icons + colors (purple decision tick, yellow question, orange assumption).
4. **Verify** the draft pane (pinned bottom) updates after at least 2 turns — when it does, the pane flashes briefly.
5. **Verify** the draft markdown renders with `## Goal`, `## Constraints`, etc. sections + bullet lists for open questions.

### 3.3 — Ready-to-ship indicator
1. Keep talking until the model decides the draft is concrete enough; it'll set `ready_to_ship=true`.
2. **Verify** the "Ready to ship" badge appears in the draft-pane header (top right).
3. **Verify** the Ship button gets a green glow ("primed" class).
4. **Verify** JARVIS does NOT auto-ship — the trigger stays with you.

### 3.4 — Ship transition
1. With a non-empty draft, click the Ship button (or say "ship it").
2. **Verify** state badge transitions to BUILDING (yellow, glowing).
3. **Verify** JARVIS speaks the Phase 3 parked message ("Shipping now, sir. The handoff into Cursor's terminal is the Phase 4 piece — for now the draft is saved and the panel has the final text.")
4. **Verify** in REPL: `sqlite3 data/jarvis.db "SELECT id, topic, status, length(final_prompt) FROM design_sessions"` shows a row with status='building' and a non-zero final_prompt length.

### 3.5 — Scrap transition
1. With a session active, click Scrap (or say "scrap this" / "start over").
2. **Verify** JARVIS speaks "Scrapped, sir. Clean slate."
3. **Verify** state badge briefly shows IDLE, then panel closes within ~1.5s.
4. **Verify** SQLite row exists with status='scrapped'.

### 3.6 — Show draft
1. Mid-conversation, say "show me the prompt".
2. **Verify** JARVIS speaks a short summary ("Goal: ...; Constraints: ...; N open questions. Full text is in the panel.") not the full markdown.
3. **Verify** no state change; conversation continues.

### 3.7 — In-design "let's design X" replaces the session
1. Mid-conversation on topic A, say "let's design something else".
2. **Verify** the existing session is dropped and a new one starts with topic = "something else".
3. **Verify** the old timeline and draft are cleared (new session_id triggers panel reset).

### 3.8 — Voice loop branches correctly
1. With a session ACTIVE, say something off-topic ("what's the weather"). The DESIGNING branch should route it through Opus design-mode (it'll likely respond with a clarifying question relating to the design).
2. End the session via scrap.
3. Say "what's the weather" again — **verify** normal Haiku path resumes (weather/casual response, not design-partner response).

### 3.9 — Self-mod detection
1. With JARVIS itself as the active project (`/Users/finley/Code/jarvis-main`), say "let's design a new event type".
2. **Verify** the opening voice reply includes "I'll be careful" (self_mod flag set).
3. **Verify** session.self_mod = True in REPL.

### 3.10 — Don't-regress
1. Without an active session, say "ship it" → **verify** it's NOT consumed as a design action (falls through to LLM; JARVIS likely asks for clarification).
2. Without an active session, say "scrap this" → same.
3. Without an active session, say "show me the prompt" → same.
4. Run the 7 Phase 1 regression phrases — verify still routing correctly.
5. Say "open jarvis-main" → verify Cursor opens + claude auto-runs via tasks.json (Phase 2 behavior).

## Phase 4 — Ship-it handoff (pending)

_Populated when Phase 4 lands._

## Phase 5 — Self-modification (pending)

_Populated when Phase 5 lands._
