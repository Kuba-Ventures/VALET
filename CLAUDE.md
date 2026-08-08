# VALET — Voice AI Assistant

## Overview
VALET (Voice-Activated Local Engineering Terminal) is a voice-first AI assistant for macOS. It runs locally on your machine, connecting to your Apple Calendar, Mail, Notes, and can spawn Claude Code sessions for development tasks.

## Quick Start
When a user clones this repo and starts Claude Code, help them:
1. Copy .env.example to .env
2. Get an Anthropic API key from console.anthropic.com
3. Get a Fish Audio API key from fish.audio
4. Install Python dependencies: pip install -r requirements.txt
5. Install frontend dependencies: cd frontend && npm install
6. Generate SSL certs: openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
7. Run the backend: python server.py
8. Run the frontend: cd frontend && npm run dev
9. Open Chrome to http://localhost:5173
10. Click to enable audio, speak to VALET

## Architecture
- **Backend**: FastAPI + Python (server.py, ~2300 lines)
- **Frontend**: Vite + TypeScript + Three.js (audio-reactive orb)
- **Communication**: WebSocket (JSON messages + binary audio)
- **AI**: Claude Haiku for fast responses, Claude Opus for research
- **TTS**: Fish Audio with VALET voice model
- **System**: AppleScript for Calendar, Mail, Notes, Terminal integration

## Key Files
- `server.py` — Main server, WebSocket handler, LLM integration, action system
- `frontend/src/orb.ts` — Three.js particle orb visualization
- `frontend/src/voice.ts` — Web Speech API + audio playback
- `frontend/src/main.ts` — Frontend state machine
- `memory.py` — SQLite memory system with FTS5 search
- `calendar_access.py` — Apple Calendar integration via AppleScript
- `mail_access.py` — Apple Mail integration (READ-ONLY)
- `notes_access.py` — Apple Notes integration
- `actions.py` — System actions (Terminal, Chrome, Claude Code)
- `browser.py` — Playwright web automation
- `work_mode.py` — Persistent Claude Code sessions
- `sports.py` — Live scores/schedules via ESPN's keyless site API (no key needed); powers [ACTION:SPORTS]. Modeled on `weather.py`.

## Environment Variables
- `ANTHROPIC_API_KEY` (required) — Claude API access
- `FISH_API_KEY` (required) — Fish Audio TTS
- `FISH_VOICE_ID` (optional) — Voice model ID
- `USER_NAME` (optional) — Your name for VALET to use
- `CALENDAR_ACCOUNTS` (optional) — Comma-separated calendar emails

## Conventions
- VALET personality: British butler, dry wit, economy of language
- Max 1-2 sentences per voice response
- Action tags: [ACTION:BUILD], [ACTION:BROWSE], [ACTION:RESEARCH], etc.
- AppleScript for all macOS integrations (no OAuth needed)
- Read-only for Mail (safety by design)
- SQLite for all local data storage

## Testing a local build

VALET is a menu-bar app you probably already have installed, and its UI is served
from the PyInstaller-bundled backend rather than `frontend/`. Both make it easy to
"verify" a fix against something that isn't your build. Each of these has cost a
session:

**1. Check which binary you're testing.** `/Applications/VALET.app` is whatever was
installed last, not what you just built — and a running orb is usually the old one.
Quit it first, then confirm:
```bash
defaults read /Applications/VALET.app/Contents/Info.plist CFBundleShortVersionString
```

**2. Sign local builds or macOS revokes every permission.** Unsigned builds get an
ad-hoc identity (`valet-c2853bf…` instead of `ai.valet.desktop`), and TCC keys on
code identity — so it's a *different app*: Accessibility / Input Monitoring / Screen
Recording all flip to "Needs setup" and re-granting won't stick.
```bash
SIGNING_IDENTITY="Developer ID Application: JAMES FINLEY UNDERWOOD (QZX7VBLDZT)" \
  ./packaging/build-macos.sh          # omit only when grants don't matter
codesign -dv --verbose=2 /Applications/VALET.app   # want: ai.valet.desktop / QZX7VBLDZT
```
Signing alone is enough locally; notarization is only for distribution.

**3. Verify against the running app, not the source.** Green `tsc` / `cargo check`,
or grepping `frontend/dist`, proves the code is *correct* — not that it *runs*. A
frontend change needs PyInstaller re-run or the app serves stale UI. To see what the
app will actually load, ask its own backend:
```bash
/Applications/VALET.app/Contents/MacOS/valet-backend &   # ~30s to answer
curl -s localhost:8340/ | grep -o '/assets/index-[^"]*\.js'   # then curl+grep that asset
```
Fast iteration: rebuild frontend + PyInstaller, then `cp dist/valet-backend` over the
app's `Contents/MacOS/valet-backend` — no cargo rebuild needed.

**Silent failure watch.** Every window JS API call (`window.__TAURI__…`) goes through
`?.` inside a swallowing `try/catch`, and these return promises, so a rejection
disappears too. A missing capability, a wrong method name, or an ACL denial no-ops and
looks like success. When a window operation "does nothing", suspect this before macOS.

## Process Event System

A real-time activity feed that drives the frontend "process panel" beside
the orb. When VALET does anything non-trivial (browses, builds, researches,
opens an app, types into a chat, dispatches to Claude Code, schedules a
calendar event), structured events flow over the WebSocket and render as a
live, holographic list. The panel auto-appears on the first event of a task
and auto-dismisses 2 seconds after the last active task finishes.

### Files
- `process_events.py` — `Event` dataclass, `ProcessEventBus` (async pub/sub),
  `task_context()` async CM, plus `emit_*` helpers.
- `frontend/src/processPanel.ts` — vanilla-TS draggable panel with per-event
  rendering and grouped `code_task` terminal blocks.
- `frontend/src/processPanel.css` — holographic palette in CSS custom props.

### Event types
| type             | when to emit                                                 |
| ---------------- | ------------------------------------------------------------ |
| `task_start`     | auto-emitted by `task_context()` on enter                    |
| `task_done`      | auto-emitted by `task_context()` on exit (status done/error) |
| `step`           | generic progress beat ("Resolving URL…", "Got 5 results")    |
| `browser_action` | each Playwright nav/click; payload includes `url`            |
| `screenshot`     | image captured; payload `path` is relative to `data/screenshots/` |
| `app_launch`     | macOS app or Finder folder opened                            |
| `text_write`     | text typed/sent into an app via System Events                |
| `code_task`      | one stdout line from a `claude -p` subprocess (streamed)     |
| `task_queued`    | something added to a future queue                            |
| `error`          | recoverable failure inside a task                            |

Each event has `task_id`, `id`, `timestamp`, `status` (`pending` / `active`
/ `done` / `error`), `title`, `detail`, and a free-form `payload` dict.

### The bus
`process_events.bus` is a module-level `ProcessEventBus` singleton. `server.py`
subscribes each WebSocket on accept (and unsubscribes on disconnect), so
events are broadcast only to currently-connected frontends.

Events go out as `{"type": "process_event", "event": {...}}` JSON frames.
`close_panel` is a separate message type sent when the user says "close it",
"dismiss", "hide that", etc. — recognized in `detect_action_fast`.

### Emitting from new code
Wrap user-visible work in `task_context` and emit child events tied to its
`task_id`:

```python
from process_events import bus as process_bus, emit_step, emit_browser_action

async def my_new_action(target: str):
    async with process_bus.task_context(f"Doing thing with {target}") as task_id:
        await emit_step(task_id, "Phase one…", status="active")
        # ... real work ...
        await emit_step(task_id, "Phase one done", status="done")
```

For long-running subprocesses, stream stdout line-by-line and emit
`code_task` per line (see `work_mode.WorkSession.send()` for the pattern).

If you write a new action handler that calls `actions.py` functions
(`open_app_or_path`, `type_into_app`, `new_cursor_project`), open a
`task_context` at the dispatch site and pass `task_id=` through — those
functions emit `app_launch` / `text_write` / `step` events when given a
task_id, no-op otherwise.

### Screenshots
Saved under `data/screenshots/<task_id>/<label>-<ts>.png` (gitignored).
The dir is served at `/screenshots/` via FastAPI `StaticFiles`. Event
`payload.path` is the path **relative** to `data/screenshots/`, so the
frontend fetches `/screenshots/<task_id>/<file>.png`.

### Panel behavior recap
- Hidden until first event arrives; slides in from the right of the orb.
- Auto-dismisses 2s after the last `task_done` if no new events have
  arrived in the meantime. Cancelled on any new event.
- Closes immediately on (a) the X button, (b) a `close_panel` server
  message, or (c) `processPanel.close()` called by another module.
- Draggable via the top handle; position persists in localStorage.
- Becomes a full-width bottom overlay below 600px viewport.

## Merge policy

This repo runs a supervised PR factory. A PR auto-merges only when the factory review
returns APPROVE-LOWRISK against this policy *and* auto-merge has been switched on (repo
variable `FACTORY_AUTOMERGE`, currently unset = off, per the Phase 3 soak). Until then
every PR is reviewed and labeled, and a human does the merge.

**Low-risk surfaces (eligible for auto-merge):**
- `frontend/src/**/*.css` and `frontend/src/processPanel.css` — styling / presentation only.
- `docs/**` — documentation.
- `README.md`, `CONTRIBUTING.md` — top-level docs.
- Purely presentational frontend TypeScript (e.g. `frontend/src/orb.ts`,
  `frontend/src/processPanel.ts`) **only when** the change is confined to visuals,
  layout, or animation and does NOT touch WebSocket message handling, voice/audio
  capture, the frontend state machine (`frontend/src/main.ts`, `frontend/src/voice.ts`),
  or any data sent to the backend. If in doubt about a `.ts` change, it escalates.

**Always escalate to a human (never auto-merge), regardless of how small the change:**
- Anything touching trust, money, auth, sessions, secrets, billing, or pricing —
  including `google_auth.py`, `google_credentials.json`, `.env*`, and the API-key paths.
- All backend Python (`server.py`, `actions.py`, `self_mod.py`, `*_access.py`,
  `memory.py`, `planner.py`, `browser.py`, `work_mode.py`, etc.) — runtime behavior,
  system control, and Calendar/Mail/Notes/Terminal/Claude-Code integrations live here.
- Database schema, migrations, or data deletion/retention (SQLite stores, `data/`).
- Access control / permissions.
- CI, workflows, build config, or dependency changes (`.github/**`, `requirements*.txt`,
  `frontend/package.json`, `frontend/package-lock.json`).
- Anything outside the low-risk surfaces above.

The reviewer (`.claude/agents/pr-reviewer.md`) is the source of truth for how this policy
is enforced. Tighten this block whenever something slips through.


<!-- BEGIN STANDARD -->
## Response style
- Lead with the concrete next action, before context or caveats.
- Number multi-step work.
- Restate what's done and what's left each turn.
- No tangents or "you might also consider."
- Time estimates as specifics ("~5 min").
- Call out completed steps explicitly.

## Design and UI work
Any product or feature change with a visual surface: present exactly three
options (A, B, C), one-line rationale each. Render them — never describe
them in prose. Build each as a working preview and open all three side by
side in a browser. `/design-shotgun` does this end to end.
Stop and wait for a choice before building anything further.

## Git workflow
- Never commit to `main`. Branch as `claude/<description>`.
- One PR per logical change — don't mix chores into feature branches.
- Delete the branch after merge.
<!-- END STANDARD -->
