# VALET
*Voice-first AI assistant for macOS with audio-reactive orb and live process panel.*

*Last updated: 2026-06-10 10:17 ET by kuba-vault*

---

## TL;DR

VALET is a local, voice-first macOS assistant — British-butler persona, audio-reactive Three.js orb, Claude under the hood, Fish Audio for TTS. It reads Calendar/Mail/Notes via AppleScript, browses the web with Playwright, and can spin up brand-new projects or modify its own repo through a design panel that pipes prompts directly into Cursor or Claude Code. The project is in post-MVP iteration; the recent window (2026-05-31 → 2026-06-08) was almost entirely about making the self-mod ship pipeline reliable (focus the right Cursor terminal, verify the Claude pane is live, fall back to a file ship, and clean up after scrapped branches), plus a render-only multi-day weather panel. As of today (2026-06-10) the repo is wired into the org's supervised PR factory (`chore/factory-init`, PR #1) ahead of a planned wave of major product changes. Self-directed, no live deploy.

---

## What it is

**The problem:** Talking to LLM chat boxes is slow. Switching between Calendar, Mail, browser tabs, terminals and a coding agent to get one thing done is slower.
**The solution:** A single voice loop on the Mac that routes intent — conversation, system lookup, research, design, ship-to-Cursor — without ever leaving the orb.
**The user:** Solo builder on macOS who already lives in Claude Code, Cursor and the Apple suite.
**The value:** Hands-free orchestration of the dev/personal stack with sub-second voice latency for the simple stuff and full Opus + web-tools for the hard stuff.

---

## Status

- **Phase:** post-MVP iteration — now entering a "major product changes" wave, with the PR factory landed first to govern it
- **Engagement manager:** self-directed
- **Lead:** Finley
- **Cadence:** continuous (per-feature commits; self-mod cuts `feature/*` branches)
- **Next milestone:** merge PR #1 (factory init) + add the `CLAUDE_CODE_OAUTH_TOKEN` secret, then start the major product changes on top of the factory
- **Flags:** on-track

---

## Where we are right now

Two things are live this window. First, the self-mod ship pipeline got hardened across 2026-05-31 → 2026-06-08: it now focuses Cursor's integrated terminal by AXDescription (the terminal element is an `AXTextField` whose description contains "terminal" — the earlier AXTextArea assumption was the ship bug), verifies a Claude pane is actually live before pasting (reading the `ps` foreground process of every Cursor terminal pty), and falls back to a staged file ship instead of dumping the prompt at a shell prompt when it can't confirm. A separate fix stopped self-mod silently dropping a ~44KB paste into the Claude pane — `project_context.py` now hard-caps warm context at 6000 chars because an oversized clipboard paste raced the Enter key and never landed. `self_mod.abandon_feature_branch()` plus a "Scrap branch" path now clean up after a shipped-but-rejected build. Second, `weather.py` gained a render-only multi-day panel — `format_voice_summary(when=…)` now answers today / tomorrow / day-after / week.

Today (2026-06-10) the repo was wired into the org's supervised PR factory, mirroring the soultech/Dharma pattern. PR #1 (`chore/factory-init`) is open against main with branch protection requiring both factory checks. It is intentionally not yet mergeable by the bot: it touches `.github/**`, so factory-review fails closed and needs an admin merge, and `CLAUDE_CODE_OAUTH_TOKEN` isn't in the repo yet. Worth attention: (1) the `.venv` still has a stale `valet-main` shebang after the local repo rename — see Risks; (2) `server.py` is now 5700 lines, still quoted as "~2300" in CLAUDE.md/README; (3) PR #1 is the gate for everything that follows.

---

## What's built

**Frontend / UI**
- Three.js particle orb that deforms to TTS audio (`frontend/src/orb.ts`)
- Live "process panel" — draggable, holographic, auto-show/auto-dismiss, MCU-styled event feed beside the orb (`frontend/src/processPanel.{ts,css}`)
- Design panel with target dropdown including "+ New project…" inline name+stack flow (`frontend/src/designPanel.{ts,css}`)
- Floating result cards: research sources, product cards, web fetches, weather widget — independent panels that tile to the right (`frontend/src/floatingPanels.ts`)
- Settings UI with hometown city, calendar accounts, API key management (`frontend/src/settings.ts`)
- Wake-phrase detection ("ok valet", "hey valet") via Web Speech API (`frontend/src/wakeWord.ts`)

**Backend / data**
- FastAPI server, single file, WebSocket loop (`server.py` — 5700 lines)
- Action system with tag-driven dispatch: `[ACTION:BUILD]`, `[ACTION:BROWSE]`, `[ACTION:RESEARCH]`, `[ACTION:CHECK_WEATHER]`, `[ACTION:PROMPT_PROJECT]`, `[ACTION:ADD_TASK]`, `[ACTION:REMEMBER]`, `[DISPATCH_TO_AGENT]`, `[ACTION:OPEN_PROJECT]`
- Fast-path intent regex matchers that bypass the LLM for common utterances (weather, "open X", "new project for X", design opt-in, cancel words)
- Native research via Claude Opus 4.7 web tools — streaming, source-preview cards, 10-min timeout, 25s voice interjections (`server.py` + chunks 7-18)
- Design partner — Opus-driven conversational spec-er that hands off to Cursor (`design_partner.py`, 674 lines)
- Self-modification machinery — VALET can edit its own repo behind a clean-tree gate, cut a `feature/*` branch, ship a prompt to the on-branch Claude pane, verify the paste landed, fall back to a staged file ship if not, and scrap the branch on rejection (`self_mod.py`, `actions.py`)
- Cursor-terminal targeting for ship — focus the integrated terminal via AXDescription, classify each Cursor terminal pty by its foreground `ps` process so a paste only fires into a confirmed live Claude pane (`actions.py` `_focus_cursor_terminal`, `_cursor_terminal_pane_summary`)
- Weather pipeline — Open-Meteo geocode + forecast, WMO code maps, severe/UV/precip alerts, and a render-only multi-day summary (today / tomorrow / day-after / week) (`weather.py`, 407 lines)
- Memory system — SQLite with FTS5 (`memory.py`, 778 lines)
- Process event bus — async pub/sub with `task_context()` async CM, broadcast to all connected WS clients (`process_events.py`)
- Calendar (`calendar_access.py`), Mail read-only (`mail_access.py`), Notes (`notes_access.py`) — all via AppleScript
- Browser automation (`browser.py`) — Playwright
- Persistent Claude Code sessions (`work_mode.py`)
- Greenfield project scaffolds — python/node/rust/go/other, all with stack-appropriate manifests + `.gitignore` + initial git commit (`actions.py` `new_cursor_project`)

**Infrastructure**
- Supervised PR factory (on `chore/factory-init` / PR #1, not yet merged) — `.github/workflows/factory.yml` with two required checks, `pr-reviewer` subagent vendored to `.claude/agents/`, merge policy in `CLAUDE.md`, dev deps in `requirements-dev.txt` (see "Supervised PR factory" below)
- macOS launchd service for auto-start (`scripts/com.valet.backend.plist`, `scripts/install-launchd.sh`)
- Local SSL via self-signed certs (`cert.pem`, `key.pem`) — required for Web Speech API over HTTPS
- Screenshots served via FastAPI StaticFiles at `/screenshots/`, dev-proxied by Vite
- Smoke test script (`scripts/smoke_test.sh`)
- Desktop overlay (Swift native) — early-stage companion UI (`desktop-overlay/ValetOverlay.swift`)

---

## Supervised PR factory  [rewrite]

*Wired up 2026-06-10, mirroring the org's soultech/Dharma pattern. Lives on branch `chore/factory-init` (PR #1), not yet merged.*

- **Workflow:** `.github/workflows/factory.yml` runs on every PR, two required checks:
  - **factory-tests** — deterministic pytest gate. Runs only the hermetic mock-based suites `tests/test_e2e_pipeline.py` + `tests/test_feedback_loop.py` (26 tests, no network / no API key). The live-LLM `test_classifier.py` and network/Playwright `test_browser_integration.py` are intentionally excluded from the gate.
  - **factory-review** — `pr-reviewer` subagent (`.claude/agents/pr-reviewer.md`, Sonnet) via `anthropics/claude-code-action`. Fail-closed: GREEN whenever the reviewer actually runs and writes a recognized verdict (`APPROVE-LOWRISK` or `ESCALATE`); RED when no real review happened (e.g. a PR touching `.github/**`, which the action skips by design).
- **Merge policy** in `CLAUDE.md` `## Merge policy`: low-risk = CSS / docs / purely-presentational frontend TS; always-escalate = all backend Python, auth/money/secrets, DB/schema, and CI/deps/workflows.
- **Branch protection** on `main`: both `factory-tests` and `factory-review` required, strict (up-to-date) enabled, `enforce_admins` off (so an admin can still merge a fail-closed PR).
- **Auto-merge is OFF.** Gated behind repo variable `FACTORY_AUTOMERGE` (unset). Stays off until a ~2-week soak (Phase 3); until then every PR is reviewed + labeled and a human does the merge.
- **Outstanding before it works end-to-end:**
  - `CLAUDE_CODE_OAUTH_TOKEN` secret must be added to `jarvis-y` (repo currently has **zero** secrets) or factory-review can't authenticate.
  - PR #1 must be merged manually by an admin — it adds `.github/**`, so it fails review closed by design.

---

## Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | FastAPI + Python 3.11+ | `server.py` (5700 lines — overdue for split) |
| Frontend | Vite 6 + TypeScript 5.7 + Three.js 0.183 | `frontend/` |
| Communication | WebSocket (JSON + binary audio) | WSS over self-signed certs |
| AI (fast) | Claude Haiku via Anthropic SDK | conversational, sub-second |
| AI (deep) | Claude Opus 4.7 with web tools | research + design partner |
| TTS | Fish Audio | VALET voice model `612b878b113047d9a770c069c8b4fdfe` |
| STT | Chrome Web Speech API | client-side, free |
| Memory | SQLite + FTS5 | `data/valet.db` |
| Browser automation | Playwright | `browser.py` |
| macOS integrations | AppleScript + Swift helper | Calendar, Mail (RO), Notes, Terminal |
| Geocode + Weather | Open-Meteo | free, no API key |
| Calendar (optional) | Google Calendar API | OAuth via `google_auth.py` |
| Hosting | local-only | runs on the user's Mac |
| CI / PR factory | GitHub Actions + `anthropics/claude-code-action` + pytest | `.github/workflows/factory.yml` (on `chore/factory-init`) |

---

## Integrations & MCPs

| Integration | Purpose | Cost | Status |
|---|---|---|---|
| Anthropic Claude API | Haiku for voice replies, Opus 4.7 for research + design + greenfield | usage-based | live |
| Fish Audio TTS | VALET-voiced spoken responses | usage-based | live |
| Open-Meteo | geocoding + weather forecasts | free | live |
| Google Calendar API | optional read of Google calendars (OAuth) | free tier | live (optional) |
| Apple Calendar / Mail / Notes | local read via AppleScript (Mail is read-only by design) | free | live |
| Playwright (Chromium) | web automation for browse / og:image enrichment | free | live |
| Claude Code CLI | dispatched sub-agent tasks via `claude -p` streamed stdout | usage-based (inherits Claude plan) | live |
| Cursor | paste target for design-panel ship handoff | external app | live |

*Source: no MCP configs found in repo. Integrations inferred from `requirements.txt`, `.env.example`, `weather.py`, `google_auth.py`, `actions.py`, `work_mode.py`.*

---

## Decisions log

- **2026-06-10 — Supervised PR factory before the major-changes wave** — Wired in the org's soultech/Dharma factory pattern (two required checks, fail-closed reviewer, branch protection) ahead of a planned wave of major product changes, so risky edits land through a governed pipeline. Auto-merge deliberately left OFF until a ~2-week soak (Phase 3); the gate runs only hermetic mock tests (live-LLM and Playwright suites excluded so no secrets/network in CI).
- **2026-06-04 — Target Cursor's terminal by AXDescription, not AXRole** — The integrated terminal's focused element is an `AXTextField` (not `AXTextArea` as earlier assumed — that was the ship bug); the palette/editor are also `AXTextField`, so AXRole can't discriminate. The terminal's AXDescription always contains "terminal", so the description is the reliable signal. Also dropped the unreliable "Focus Terminal" palette command for `Ctrl+\`` toggling.
- **2026-06-04 — Verify the Claude pane is live before pasting, else file-ship** — A paste lands in whichever Cursor terminal has focus, and AppleScript can't tell which tty that is. So classify every Cursor terminal pty by its foreground `ps` process: only paste when a pane is confirmed running `claude`; if any pane is a bare shell (Claude exited or a fresh terminal spawned), fall back to a staged file ship rather than dumping the prompt at a `%` prompt. The spoken confirmation is now honest about whether the paste was verified.
- **2026-06-03 — Hard-cap warm context at 6000 chars** — Self-mod ship silently dropped a ~44KB paste into the Claude pane: a clipboard paste that large races the Enter key and never lands. `project_context.py` now truncates composed context (`max_context_chars`, configurable in `config/design_partner.json`) with a visible marker so the ship prompt stays inside what auto-paste can reliably deliver.
- **2026-05-22 — Dedicated weather pipeline instead of generic research** — Weather questions used to route through `[ACTION:RESEARCH]` (Haiku → Opus WebFetch on weather.gov), which was slow and noisy. Replaced with `[ACTION:CHECK_WEATHER]` + Open-Meteo (free, structured, no API key) and a purpose-built floating card.
- **2026-05-21 — Greenfield projects scaffold before first prompt** — `new_cursor_project` runs an initial `git add -A && git commit` with explicit `-c user.name/email` overrides so the clean-tree gate doesn't block the first ship.
- **2026-05-21 — Greenfield bypass for self-mod gate** — Brand-new projects skip the self-modification gate because the path isn't `VALET_REPO`.
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

- [ ] Add `CLAUDE_CODE_OAUTH_TOKEN` secret to `jarvis-y` (repo has zero secrets) so factory-review can authenticate — owner: Finley
- [ ] Admin-merge PR #1 (`chore/factory-init`) — fails review closed by design (touches `.github/**`) — owner: Finley (admin)
- [ ] Fix stale `.venv` `valet-main` shebang after the local repo rename (rebuild venv or repoint) — owner: Finley
- [ ] Split `server.py` (5700 lines) — likely along action-handler boundaries — owner: Finley
- [ ] Update README.md + CLAUDE.md line-count references (still say "~2300 lines") — owner: Finley
- [ ] Verify chunk 24 design_partner Sonnet→Opus revert actually happened post-529 incident — owner: Finley
- [ ] Add demo GIF/screenshot to README (TODO marker in line 11) — owner: Finley
- [ ] Scope the upcoming "major product changes" wave — owner: Finley
- [ ] After ~2-week factory soak, decide whether to flip `FACTORY_AUTOMERGE` on (Phase 3) — owner: Finley

---

## Risks & known issues

- **Stale `.venv` shebang after repo rename** — the local repo was renamed `valet-main` → `valet` (GitHub remote is `jarvis-y`), but `.venv/bin/pip*` (and other console scripts) still hardcode `#!/Users/finley/Code/VALET-main/.venv/bin/python3.12`. Those wrappers will break until the venv is rebuilt or repointed; `.venv/bin/python -m pip …` still works as a workaround.
- **Factory not yet functional end-to-end** — factory-review will fail on every PR until `CLAUDE_CODE_OAUTH_TOKEN` is added; main is protected on a check that currently can't pass, so all merges need an admin until that secret lands and PR #1 is in.
- `server.py` size (5700 lines) is a maintainability risk — any cross-cutting refactor touches the whole file, and it's an always-escalate surface under the new merge policy
- Self-mod ship path is sensitive to the Cursor build's accessibility tree (`AXFocusedUIElement` / AXDescription) and to the `ps` process layout of integrated terminals; a Cursor update could move either out from under it
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
- **GitHub:** https://github.com/Kuba-Ventures/jarvis-y (org remote; local dir is `~/Code/VALET`)
- **PR #1 (factory init):** https://github.com/Kuba-Ventures/jarvis-y/pull/1
- **Client Drive folder:** n/a
- **Slack channel:** n/a
- **Related repos:** Kuba-Ventures soultech / Dharma (the PR-factory pattern this repo mirrors)

---

## Changelog

- **2026-06-10:** kuba-vault refresh — caught up 9 commits (2026-05-31 → 2026-06-08): self-mod ship reliability (AXDescription terminal focus, live-pane verify + file-ship fallback, 44KB paste fix, scrap-branch loop), render-only multi-day weather panel, repo-rename cleanup. Recorded the supervised PR factory wired up today (PR #1, factory.yml + 2 required checks, branch protection, merge policy); flagged the missing `CLAUDE_CODE_OAUTH_TOKEN` secret and the stale `valet-main` venv shebang.
- **2026-06-10:** Supervised PR factory wired up — `chore/factory-init` / PR #1, `factory.yml` (factory-tests: 26 mock tests; factory-review: fail-closed pr-reviewer subagent), merge policy in CLAUDE.md, pr-reviewer vendored to `.claude/agents/`, branch protection on main requiring both checks. Auto-merge OFF until a ~2-week soak.
- **2026-06-04:** Self-mod ship hardened — focus Cursor terminal by AXDescription, verify the Claude pane is live before paste (else file-ship), honest spoken confirmation, `abandon_feature_branch()` + scrap-branch path.
- **2026-06-03:** Render-only multi-day weather panel (`format_voice_summary(when=…)`: today/tomorrow/day-after/week); ship-phrase fix (`_phrase_hit` strips filler); repo-rename cleanup (`valet-main` → `valet` references).
- **2026-05-31:** Fixed self-mod silently dropping a ~44KB paste into the Claude pane — `project_context.py` hard-caps warm context at 6000 chars.
- **2026-05-26:** kuba-vault refresh — no new commits since 2026-05-22, working tree clean; bumped timestamp, flagged 4-day quiet stretch and 42 unpushed commits.
- **2026-05-22:** kuba-vault initial PROJECT.md superdoc — scanned repo, reconciled README/CLAUDE.md against current state, summarized chunks 0-33.
- **2026-05-22:** chunk 33 shipped — native `[ACTION:CHECK_WEATHER]` via Open-Meteo, floating weather card with 7-day strip + alert banner, hometown_city preference.
- **2026-05-21:** chunk 32 shipped — greenfield projects: design + scaffold + ship in one flow, stack picker (python/node/rust/go/other).
- **2026-05-21:** chunk 31 shipped — `_looks_like_app()` matcher fixes "open X" mis-routing to OPEN_PROJECT.
- **2026-05-21:** chunk 30 shipped — strip em-dashes from runtime LLM responses.
- **2026-05-21:** chunk 29 shipped — demo prep: style-steward sweep, panel auto-close, web-app routing fix.
- **2026-05-21:** chunk 28 shipped — runtime self-introspection + `[DISPATCH_TO_AGENT]` tag + Claude-Code typeinto reroute.
- **2026-05-21:** chunk 27 shipped — Claude Code sub-agent dispatch + design panel build-view bug fix.
- **2026-05-21:** chunk 26 shipped — "hey valet" wake-phrase alias.
- **2026-05-19:** chunks 21-25 shipped — voice → Claude Code via auto-paste (Mode 1) and dictation (Mode 2), self-mod paste route, design-panel paste-target dropdown.
- **2026-05-18:** chunks 19-20 shipped — design routing diagnosis + explicit design-mode opt-in.
- **2026-05-17:** chunks 7-18 shipped — native Opus 4.7 research, streaming source-preview cards, floating result panels, USD-only price guard, card lifecycle.
- **2026-05-17:** chunks 0-6 shipped — baseline phases 1-5 (process panel, haiku middleware, result cards, ship-it handoff, self-modification machinery).
