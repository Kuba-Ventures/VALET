# OVERNIGHT HALT — dirty working tree

**Timestamp:** 2026-05-17T23:19:22-0400
**HEAD on `main`:** `76ffa85 Instrument self_work_and_notify for the process panel`
**Branch created:** NONE — halted before `git checkout -b`.
**Files I touched during halt:** this note only (uncommitted). No code changes, no commits, no branch operations.

---

## Why I halted

Working rules for the overnight build, Chunk 0:

> Before you do anything, `git checkout -b overnight/<auto-slug>` from main. If the working tree is dirty, halt immediately, write `OVERNIGHT_HALT_dirty_tree.md` at repo root with `git status` output, and stop.

The tree is heavily dirty. **All of the Phase 1, Phase 2, and Phase 3 work from today's session is uncommitted on `main`.** None of it has landed. The overnight plan assumes a clean main as starting point; if I branch from main now, the branch starts at `76ffa85` (pre-Phase-1) with all of today's work tagging along as uncommitted diffs. That's not what your plan implies, so I refuse to guess.

Per the working rule "When in doubt, halt and write notes" + "A halted run with clear diagnostic notes is vastly better than a broken Jarvis at 7am" — stopping cleanly.

---

## Full git status at halt time

```
On branch main

Modified (excluding logs):
  M actions.py            +568 lines  (project lifecycle, register_project, _ensure_cursor_task, _position_cursor, _spawn_external_terminal, _fuzzy_suggestions)
  M frontend/index.html   +3 lines    (design-panel-root mount div)
  M frontend/src/main.ts  +15 lines   (designPanel wiring, ship/scrap button handlers, design_event routing)
  M memory.py             +231 lines  (SCHEMA_VERSION=2, _run_migrations, project_aliases + design_sessions tables, _normalize_project_key, _keys_match, resolve/record/touch/delete/update alias helpers, cleanup_stale_aliases, list_known_projects)
  M process_events.py     +36 lines   (emit_project_event, emit_context_event)
  M requirements.txt      +1 line     (watchdog>=4.0)
  M server.py             +693 lines  (OPEN_PROJECT / LIST_PROJECTS / REFRESH_CONTEXT / START_DESIGN / SHIP_DESIGN / SCRAP_DESIGN / SHOW_DRAFT action tags + handlers + regex fast-paths + dispatcher branches; DESIGNING voice_handler branch; _speak/_handle_pending_offer; _execute_open_project/_execute_refresh_context/_execute_start_design/etc; cleanup_stale_aliases startup; fabrication guardrail in system prompt)

Untracked:
  ?? config/                    (config/design_partner.json — window_layout, warm_context, design_session, self_mod, project_roots, new_project_root)
  ?? data/logs/                 (.gitkeep — log dir placeholder)
  ?? design_partner.py          (Phase 3 — DraftPrompt + DesignSession + Opus forced tool-use loop + per-WS registry + persist)
  ?? docs/                      (design_partner_tests.md — 26 manual test scenarios across Phase 0-3)
  ?? frontend/src/designPanel.css   (Phase 3 — --dp-* token palette, dock-left, timeline + draft pane)
  ?? frontend/src/designPanel.ts    (Phase 3 — createDesignPanel factory, markdown renderer, drag, ship/scrap buttons)
  ?? project_context.py         (Phase 2 — ProjectContext + load/refresh/get/get_active + watchdog observer + debounce)
```

(Logs intentionally omitted — they're auto-modified by the running server.)

---

## What this represents

Three completed phases of the design-partner plan, all smoke-tested and verified live but never committed:

- **Phase 1**: project lifecycle commands (`open <name>` / `new project for X` / `list my projects` / `register <path> as X`) — including post-Phase-1 hardening: camelcase normalization, `_keys_match` hyphen-tolerant fallback, multi-root `project_roots` + `new_project_root` config, stale-alias auto-repair (silent self-heal at startup and runtime), register-on-miss with fuzzy suggestions via per-WS `pending_offer` machinery, broader regex-based fast-path routing.
- **Phase 2**: warm context loader (`project_context.py`, `watchdog` file observer with 500ms debounce, `context.*` events, `refresh context` voice command, CLAUDE.md no-clobber fix).
- **Phase 3**: Design Panel centerpiece (`design_partner.py` with forced `design_turn` tool-use Opus loop, schema-v2 `design_sessions` table, DESIGNING branch in voice_handler bypassing Haiku, `frontend/src/designPanel.{ts,css}` mirroring processPanel pattern, ship/scrap/show-draft fast-actions gated on session presence). Ship transitions to BUILDING and persists the prompt — Phase 4 was explicitly parked here.

Also includes the Cursor-tasks.json scaffolding + external-Terminal removal (moved to `_spawn_external_terminal` for future use), and the fabrication guardrail in the LLM system prompt.

---

## Three ways forward — your call

1. **Commit Phase 1-3 to main first, then re-run the overnight build.**
   ```
   git add memory.py actions.py server.py process_events.py requirements.txt \
           frontend/index.html frontend/src/main.ts \
           project_context.py design_partner.py \
           frontend/src/designPanel.ts frontend/src/designPanel.css \
           config/ docs/ data/logs/.gitkeep
   # review the staged set, then:
   git commit -m "Phases 1-3: project lifecycle, warm context, design partner"
   # then re-issue the overnight build prompt; it'll branch from a clean main.
   ```
   This is the cleanest path. `git log` reads "Phase 1-3 base" → overnight chunks.

2. **Branch from current HEAD (not main) and proceed.**
   Tell me explicitly: *"start overnight from current HEAD, not main — keep all uncommitted work and make chunk 0's first commit be a `chunk 0: phase 1-3 baseline` containing everything currently dirty, then continue."*
   That gives you the overnight chunks layered on top of one big baseline commit, with everything tracked on the overnight branch but nothing landing on main. You merge to main in the morning after review.

3. **Reset everything and start the overnight from a truly clean main.**
   Catastrophic — would discard ~2500 lines of working, smoke-tested Phase 1-3 code. Don't pick this unless you mean it.

Recommendation: **path 1** if you trust the Phase 1-3 work as-reviewed and want a clean history; **path 2** if you'd rather defer review of Phase 1-3 until you wake up.

---

## What I did NOT do

- Did not create the `overnight/*` branch.
- Did not commit anything.
- Did not modify any code file (this halt note is the only filesystem write).
- Did not restart the server.
- Did not start Chunks 1–6.

Live server PID 21246 still running Phase 3 code (the post-Phase-3 restart). It'll keep working until you decide a path forward.

---

## Suggested re-prompt (if you pick path 2)

> Start the overnight build from current HEAD instead of main. First commit on the overnight branch should be `chunk 0: phase 1-3 baseline` containing all currently uncommitted work. Then proceed with chunks 1–6 as originally written.

Stopping here. Awaiting your call.
