# JARVIS
*Voice-first AI assistant for macOS with audio-reactive orb and live process panel.*

*Last updated: 2026-05-26 10:50 ET by kuba-vault*

---

## TL;DR

JARVIS is a local, voice-first macOS assistant — British-butler persona, audio-reactive Three.js orb, Claude under the hood, Fish Audio for TTS. It reads Calendar/Mail/Notes via AppleScript, browses the web with Playwright, and can spin up brand-new projects or modify existing ones through a design panel that pipes prompts directly into Cursor or Claude Code. The project is in post-MVP iteration, shipping in numbered "chunks" — chunks 29-33 (demo-prep cluster) landed Thu-Fri last week; the repo has been quiet since chunk 33 on 2026-05-22. Demo-ready, self-directed, no live deploy.

---

## What it is

**The problem:** Talking to LLM chat boxes is slow. Switching between Calendar, Mail, browser tabs, terminals and a coding agent to get one thing done is slower.
**The solution:** A single voice loop on the Mac that routes intent — conversation, system lookup, research, design, ship-to-Cursor — without ever leaving the orb.
**The user:** Solo builder on macOS who already lives in Claude Code, Cursor and the Apple suite.
**The value:** Hands-free orchestration of the dev/personal stack with sub-second voice latency for the simple stuff and full Opus + web-tools for the hard stuff.

---

## Status

- **Phase:** post-MVP iteration
- **Engagement manager:** self-directed
- **Lead:** Finley
- **Cadence:** continuous (chunked commits)
- **Next milestone:** TBD — chunks 29-33 were a demo-prep cluster; no committed roadmap past chunk 33
- **Flags:** on-track (no commits since chunk 33 on 2026-05-22 — first 4-day quiet stretch since chunk 7)

---

## Where we are right now

Repo is quiet — 4 days since chunk 33 (the longest gap since chunk 7), working tree clean, 42 commits ahead of origin/main but no push. The demo-prep cluster (chunks 29-33) closed out with native weather (Open-Meteo widget + alerts), greenfield project scaffolds, and the `_looks_like_app()` matcher fixing "open X" mis-routing. The design panel is a complete loop now: pick or invent a project, talk through the spec, ship it into Cursor or Claude Code. The voice fast-path is dense — `_NEW_PROJECT_DESIGN_PATTERN`, `_looks_like_app()`, weather regexes — keeping latency-sensitive utterances off Haiku entirely. No active bugs. Worth attention: (1) `server.py` is 5504 lines, well past the "~2300 lines" still quoted in CLAUDE.md and README — the file is overdue for a split; (2) 42 unpushed commits on main; (3) post-chunk-33 milestone still undecided.

---

## What's built

**Frontend / UI**
- Three.js particle orb that deforms to TTS audio (`frontend/src/orb.ts`)
- Live "process panel" — draggable, holographic, auto-show/auto-dismiss, MCU-styled event feed beside the orb (`frontend/src/processPanel.{ts,css}`)
- Design panel with target dropdown including "+ New project…" inline name+stack flow (`frontend/src/designPanel.{ts,css}`)
- Floating result cards: research sources, product cards, web fetches, weather widget — independent panels that tile to the right (`frontend/src/floatingPanels.ts`)
- Settings UI with hometown city, calendar accounts, API key management (`frontend/src/settings.ts`)
- Wake-phrase detection ("ok jarvis", "hey jarvis") via Web Speech API (`frontend/src/wakeWord.ts`)

**Backend / data**
- FastAPI server, single file, WebSocket loop (`server.py` — 5504 lines)
- Action system with tag-driven dispatch: `[ACTION:BUILD]`, `[ACTION:BROWSE]`, `[ACTION:RESEARCH]`, `[ACTION:CHECK_WEATHER]`, `[ACTION:PROMPT_PROJECT]`, `[ACTION:ADD_TASK]`, `[ACTION:REMEMBER]`, `[DISPATCH_TO_AGENT]`, `[ACTION:OPEN_PROJECT]`
- Fast-path intent regex matchers that bypass the LLM for common utterances (weather, "open X", "new project for X", design opt-in, cancel words)
- Native research via Claude Opus 4.7 web tools — streaming, source-preview cards, 10-min timeout, 25s voice interjections (`server.py` + chunks 7-18)
- Design partner — Opus-driven conversational spec-er that hands off to Cursor (`design_partner.py`, 674 lines)
- Self-modification machinery — JARVIS can edit its own repo behind a clean-tree gate (`self_mod.py`)
- Weather pipeline — Open-Meteo geocode + forecast, WMO code maps, severe/UV/precip alert synthesis (`weather.py`, 361 lines)
- Memory system — SQLite with FTS5 (`memory.py`, 778 lines)
- Process event bus — async pub/sub with `task_context()` async CM, broadcast to all connected WS clients (`process_events.py`)
- Calendar (`calendar_access.py`), Mail read-only (`mail_access.py`), Notes (`notes_access.py`) — all via AppleScript
- Browser automation (`browser.py`) — Playwright
- Persistent Claude Code sessions (`work_mode.py`)
- Greenfield project scaffolds — python/node/rust/go/other, all with stack-appropriate manifests + `.gitignore` + initial git commit (`actions.py` `new_cursor_project`)

**Infrastructure**
- macOS launchd service for auto-start (`scripts/com.jarvis.backend.plist`, `scripts/install-launchd.sh`)
- Local SSL via self-signed certs (`cert.pem`, `key.pem`) — required for Web Speech API over HTTPS
- Screenshots served via FastAPI StaticFiles at `/screenshots/`, dev-proxied by Vite
- Smoke test script (`scripts/smoke_test.sh`)
- Desktop overlay (Swift native) — early-stage companion UI (`desktop-overlay/JarvisOverlay.swift`)

---

## Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | FastAPI + Python 3.11+ | `server.py` (5504 lines — overdue for split) |
| Frontend | Vite 6 + TypeScript 5.7 + Three.js 0.183 | `frontend/` |
| Communication | WebSocket (JSON + binary audio) | WSS over self-signed certs |
| AI (fast) | Claude Haiku via Anthropic SDK | conversational, sub-second |
| AI (deep) | Claude Opus 4.7 with web tools | research + design partner |
| TTS | Fish Audio | JARVIS voice model `612b878b113047d9a770c069c8b4fdfe` |
| STT | Chrome Web Speech API | client-side, free |
| Memory | SQLite + FTS5 | `data/jarvis.db` |
| Browser automation | Playwright | `browser.py` |
| macOS integrations | AppleScript + Swift helper | Calendar, Mail (RO), Notes, Terminal |
| Geocode + Weather | Open-Meteo | free, no API key |
| Calendar (optional) | Google Calendar API | OAuth via `google_auth.py` |
| Hosting | local-only | runs on the user's Mac |

---

## Integrations & MCPs

| Integration | Purpose | Cost | Status |
|---|---|---|---|
| Anthropic Claude API | Haiku for voice replies, Opus 4.7 for research + design + greenfield | usage-based | live |
| Fish Audio TTS | JARVIS-voiced spoken responses | usage-based | live |
| Open-Meteo | geocoding + weather forecasts | free | live |
| Google Calendar API | optional read of Google calendars (OAuth) | free tier | live (optional) |
| Apple Calendar / Mail / Notes | local read via AppleScript (Mail is read-only by design) | free | live |
| Playwright (Chromium) | web automation for browse / og:image enrichment | free | live |
| Claude Code CLI | dispatched sub-agent tasks via `claude -p` streamed stdout | usage-based (inherits Claude plan) | live |
| Cursor | paste target for design-panel ship handoff | external app | live |

*Source: no MCP configs found in repo. Integrations inferred from `requirements.txt`, `.env.example`, `weather.py`, `google_auth.py`, `actions.py`, `work_mode.py`.*

---

## Decisions log

- **2026-05-22 — Dedicated weather pipeline instead of generic research** — Weather questions used to route through `[ACTION:RESEARCH]` (Haiku → Opus WebFetch on weather.gov), which was slow and noisy. Replaced with `[ACTION:CHECK_WEATHER]` + Open-Meteo (free, structured, no API key) and a purpose-built floating card.
- **2026-05-21 — Greenfield projects scaffold before first prompt** — `new_cursor_project` runs an initial `git add -A && git commit` with explicit `-c user.name/email` overrides so the clean-tree gate doesn't block the first ship.
- **2026-05-21 — Greenfield bypass for self-mod gate** — Brand-new projects skip the self-modification gate because the path isn't `JARVIS_REPO`.
- **2026-05-21 — Smart `_looks_like_app()` matcher** — Fast-path was firing `OPEN_PROJECT` for any "open X" utterance whose target wasn't a literal entry in `_OPEN_APP_NAMES`. STT mishearings like "work gmail" got mis-routed; the matcher now distinguishes app-shaped tokens from project-shaped ones before dispatch.
- **2026-05-21 — Strip em-dashes from LLM output at runtime** — Em-dashes were a giveaway tell of LLM-generated text; chunk 30 strips them from runtime responses to keep voice transcripts and on-screen text feeling human.
- **2026-05-20 — Design-mode requires explicit opt-in (Option C)** — Chunk 19 diagnosed false-positive design routing; chunk 20 made design mode require an explicit phrase rather than inferring from intent.
- **2026-05-19 — Auto-paste over file-default for ship handoff** — Mode 1 (auto-paste into target IDE) wins over Mode 2 (dictation). Chunk 21 shipped voice → Claude Code via auto-paste; chunks 22-23 cleaned up the `ship_method` whitelist regression.
- **2026-05-18 — Temporarily swapped design_partner Opus → Sonnet during 529 incident** — Documented as a temp swap during an Anthropic 529 incident (chunk 24). Should be re-verified that the swap was reverted.
- **2026-05-17 — Native research via Opus 4.7 web tools, no scratch folders** — Replaced the older folder-based research output with native Opus web tools and streaming source-preview cards (chunk 7+).
- **Architectural — Mail is read-only by design** — All Apple Mail integration is read-only. Sending mail is intentionally out of scope.
- **Architectural — AppleScript over OAuth** — Calendar/Mail/Notes all use native AppleScript so no token management, no consent flows. Google Calendar is the one exception.
- **Architectural — Single `server.py` file** — All backend logic lives in one file. Conscious tradeoff for speed of iteration; the file is now 5504 lines and overdue for a split.

---

## Open loops

- [ ] Split `server.py` (5504 lines) — likely along action-handler boundaries — owner: Finley
- [ ] Update README.md + CLAUDE.md line-count references (still say "~2300 lines") — owner: Finley
- [ ] Verify chunk 24 design_partner Sonnet→Opus revert actually happened post-529 incident — owner: Finley
- [ ] Add demo GIF/screenshot to README (TODO marker in line 11) — owner: Finley
- [ ] Decide next milestone post-chunk-33 — owner: Finley
- [ ] No tests added for chunks 31-33 voice fast-path regexes — owner: Finley
- [ ] 42 commits ahead of origin/main, unpushed — owner: Finley

---

## Risks & known issues

- `server.py` size is a maintainability risk — any cross-cutting refactor touches the whole file
- Self-signed certs require manual Chrome trust on first run; not documented in `README.md` beyond the openssl command
- Self-modification machinery (`self_mod.py`) gates on clean tree; runtime logs previously tripped it — chunks 7b/7c/7d added pathspec exclusions, but the surface is fragile
- Voice fast-path regex set is growing; risk of overlap/order-sensitivity (chunk 31 already had to fix `_NEW_PROJECT_DESIGN_PATTERN` matching before `_START_DESIGN_PATTERN`)
- Background context thread re-hits geocode every 30s if `_ctx_cache["_weather_geo"]` clears; documented but not load-tested
- No CI — tests in `tests/` are run manually
- Fish Audio is single-vendor for TTS with no fallback configured
- 529s from Anthropic during the design panel turn caused chunk 24's emergency Sonnet swap; no resilience layer added since

---

## Links

- **Live URL:** local-only (`http://localhost:5173`)
- **Staging:** n/a
- **Client Drive folder:** n/a
- **Slack channel:** n/a
- **Related repos:** none committed; `jarvis-y/` subdirectory present but untracked

---

## Changelog

- **2026-05-26:** kuba-vault refresh — no new commits since 2026-05-22, working tree clean; bumped timestamp, flagged 4-day quiet stretch and 42 unpushed commits.
- **2026-05-22:** kuba-vault initial PROJECT.md superdoc — scanned repo, reconciled README/CLAUDE.md against current state, summarized chunks 0-33.
- **2026-05-22:** chunk 33 shipped — native `[ACTION:CHECK_WEATHER]` via Open-Meteo, floating weather card with 7-day strip + alert banner, hometown_city preference.
- **2026-05-21:** chunk 32 shipped — greenfield projects: design + scaffold + ship in one flow, stack picker (python/node/rust/go/other).
- **2026-05-21:** chunk 31 shipped — `_looks_like_app()` matcher fixes "open X" mis-routing to OPEN_PROJECT.
- **2026-05-21:** chunk 30 shipped — strip em-dashes from runtime LLM responses.
- **2026-05-21:** chunk 29 shipped — demo prep: style-steward sweep, panel auto-close, web-app routing fix.
- **2026-05-21:** chunk 28 shipped — runtime self-introspection + `[DISPATCH_TO_AGENT]` tag + Claude-Code typeinto reroute.
- **2026-05-21:** chunk 27 shipped — Claude Code sub-agent dispatch + design panel build-view bug fix.
- **2026-05-21:** chunk 26 shipped — "hey jarvis" wake-phrase alias.
- **2026-05-19:** chunks 21-25 shipped — voice → Claude Code via auto-paste (Mode 1) and dictation (Mode 2), self-mod paste route, design-panel paste-target dropdown.
- **2026-05-18:** chunks 19-20 shipped — design routing diagnosis + explicit design-mode opt-in.
- **2026-05-17:** chunks 7-18 shipped — native Opus 4.7 research, streaming source-preview cards, floating result panels, USD-only price guard, card lifecycle.
- **2026-05-17:** chunks 0-6 shipped — baseline phases 1-5 (process panel, haiku middleware, result cards, ship-it handoff, self-modification machinery).
