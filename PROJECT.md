# VALET
*Voice-Activated Local Engineering Terminal — a downloadable, license-gated macOS voice assistant.*

*Last updated: 2026-06-10 18:00 ET by kuba-vault*

---

## TL;DR  [rewrite]

VALET (formal name; answers to "Vee") is a local, voice-first macOS assistant with a British-butler persona, an audio-reactive Three.js orb, and a live process panel. This session ran a "Phase 2 re-scaffold" that turned the local prototype into a sellable, downloadable product: a hosted, license-gated proxy now holds all vendor keys server-side (Stage A, live in prod), the app routes every AI + TTS call through that proxy behind a license gate with a 7-day offline grace window (Stage B), control was rebuilt on a portable `ActionExecutor` (Stage C) wrapped in a risk-tiered safety layer with a kill switch and confirm-first deletes (Stage D), and the assistant got two British voices plus a hard cut of self-editing from shipped builds (Stage E). The JARVIS name and all Marvel/MCU framing were retired this session — the assistant is VALET / Vee everywhere in the running product. Only **Stage F (Tauri packaging + signed/notarized macOS app + first-run onboarding + telemetry)** remains before real distribution. Top open risk: every shipped user currently bills the owner's personal Anthropic + Fish keys, and the fair-use cap is soft (never blocks) and unvalidated — close before distributing.

---

## What it is  [rewrite when value prop evolves]

**The problem:** Talking to LLM chat boxes is slow, and stitching together Calendar, Mail, a browser, terminals and a coding agent to get one thing done is slower. Shipping that as a product also means no end user should have to paste in their own API keys.
**The solution:** A single voice loop on the Mac that routes intent — conversation, system lookup, research, file/app control, ship-to-Cursor — without leaving the orb, with all AI billed centrally behind a license so the download ships with zero vendor secrets.
**The user:** A macOS builder who lives in Claude Code, Cursor and the Apple suite and wants hands-free orchestration — and, post-Phase-2, a paying customer who buys a license and downloads a signed app.
**The value:** Sub-second voice for the simple stuff, full Opus + web tools for the hard stuff, native macOS control with a safety net — and a clean install with no key management.

---

## Status  [rewrite]

- **Phase:** post-MVP iteration — Phase 2 re-scaffold (product-ization) shipped; one stage (F: packaging) to go before distribution.
- **Engagement manager:** self-directed
- **Lead:** Finley
- **Cadence:** continuous (per-stage PRs through the supervised factory)
- **Next milestone:** Stage F — Tauri package (PyInstaller-bundled FastAPI + built frontend) into one signed, notarized macOS app that passes Gatekeeper, plus first-run permission onboarding and telemetry. No hard date.
- **Flags:** shipping

---

## Where we are right now  [rewrite]

This session re-scaffolded the local prototype into a sellable product across five stages, all landed today (2026-06-10). Stage A stood up the license-gated proxy in the sibling `product-site/` and it is live in prod on Vercel — it holds the Anthropic + Fish keys, validates each call against the Supabase `licenses` table, meters per-license usage against a soft fair-use cap, and traces to Langfuse. Stage B repointed the app: `server.py` sends every model call to the proxy via the Anthropic SDK `base_url` with the license in `X-License-Key`, TTS goes to `/api/proxy/tts`, and the new `licensing.py` gates the assistant loop with a 7-day offline grace window; the in-app API-key entry was removed. Stage C added a portable `ActionExecutor` (macOS AppleScript backend), Stage D wrapped it in a risk-tiered safety engine (Tier 0 auto / Tier 1 confirm, kill switch, Trash-safe deletes), and Stage E added two British voices and excluded self-editing from shipped builds. The JARVIS → VALET rename is merged everywhere in the product. The one remaining piece is Stage F (packaging). The thing that needs the human's attention before any real distribution: the bundled model bills the owner's personal Anthropic + Fish keys for every shipped user, and the fair-use cap only soft-warns — see Risks.

---

## What's built  [rewrite]

### Stage A — Proxy spine (LIVE in prod)
- License-gated AI/TTS proxy in `product-site/` (Next.js on Vercel, https://jarvis-y.vercel.app). Routes: `app/api/proxy/{completion,research,tts,usage}` plus a native Anthropic-dialect `app/api/proxy/v1/messages`.
- Backed by `product-site/lib/proxy/{auth,pricing,usage,langfuse,anthropic,fish}.ts`. Anthropic + Fish keys live server-side; the downloadable app ships with no vendor secrets.
- Every call validated against the Supabase `licenses` table; usage metered per license (requests, tokens, est. cost) against a fair-use allowance with **soft-warn** launch behavior (default `$8/month`, never blocks — `FAIR_USE_MONTHLY_USD`).
- Traced to Langfuse with the license as user id, payloads scrubbed by default.
- `product-site/supabase/migration_usage.sql` adds the `license_usage` table + atomic `record_usage()` RPC (30-day rolling, lazy reset). Zero new npm deps.

### Stage B — App repoint + license gate (LIVE)
- `server.py` routes all AI + TTS through the proxy: Anthropic SDK `base_url` → `{PROXY_BASE_URL}/api/proxy`, license in `X-License-Key` (`server.py:2930`); `synthesize_speech` → `/api/proxy/tts` (`server.py:2460`).
- `licensing.py` validates against `/api/license/validate` with a 7-day offline grace window (state in `data/license_state.json`); the assistant loop refuses when not entitled.
- App stores only `LICENSE_KEY` + `PROXY_BASE_URL`; in-app API-key entry removed. Settings now expose License Key + Proxy URL + a `test-license` action.
- Dev fallback (no `LICENSE_KEY` → direct keys) preserved for the internal repo only.
- Also extracted from `server.py`: `task_manager.py`, `voice_text.py`, `project_scanner.py`. The tightly-coupled voice-dispatch core (`voice_handler` + `_execute_*` handlers + lookups) was deliberately left in `server.py`.

### Stage C — Control layer (MERGED)
- Portable `action_executor.py` — `ActionExecutor` ABC + `ActionResult` + `Capability` enum (open app/path, read/write/move/delete file, list folder, run app command, send keystroke, navigate, run script).
- `applescript_executor.py` — macOS backend. Detects app scriptability via the bundle's `.sdef`/Info.plist without launching; non-scriptable apps return a structured, logged `not_supported`. Deletes go to Trash. Portable so a future Windows/Linux (or Accessibility/vision) backend is a swap.

### Stage D — Risk-tiered safety (MERGED)
- `safety.py` — `classify()` → Tier 0 (auto) / Tier 1 (confirm); `KillSwitch`; `ConfirmationManager`. Tier 0 = reads/lists/opens/navigate; Tier 1 = delete/move/overwrite, drive apps, keystrokes, scripts, bulk ops, protected paths. New-file write is Tier 0; overwrite is Tier 1.
- `safe_executor.py` — `SafeExecutor` decorator over the Stage C executor (confirm-first, Trash-safe, kill-aware).
- `server.py` routes `[ACTION:DELETE_FILE]` + raw `[ACTION:APPLESCRIPT]` through it.
- Frontend `confirmCard.{ts,css}` (holographic Allow/Deny card) + an always-available STOP kill-switch button.
- REST `/api/safety/{kill, kill/reset, status}` + WS confirm/kill round-trip.

### Stage E — Voices + cut self-editing (MERGED, PR #17)
- Two selectable British voices (Male/Female), persona unchanged — `VALET_VOICE` + `VALET_VOICE_{MALE,FEMALE}_ID`; `_active_voice_id()` reads live and is sent as the Fish `reference_id` (applies on the next reply, no restart). Settings has a Male/Female toggle. Both voice IDs set (male `612b878b…`, female `b347db033a6549378b48d00acb0d06cd`).
- Self-editing disabled in shipped builds — all `self_mod` access routes through `_load_self_mod()`, which returns `None` when shipped (`VALET_SHIPPED` set or no `.git`), so `self_mod.py` is excludable. Dev-repo behavior unchanged.

### Carried over (pre-Phase-2, still live)
- **Frontend / UI:** audio-reactive Three.js orb (`frontend/src/orb.ts`); draggable holographic process panel (`processPanel.{ts,css}`); design panel with "+ New project…" flow (`designPanel.{ts,css}`); floating result cards (`floatingPanels.ts`); settings (`settings.ts`); wake-phrase detection — "ok vee" / "hey vee" + soft "vee?" (`wakeWord.ts`).
- **Backend / data:** FastAPI WebSocket server (`server.py`); tag-driven action dispatch (`[ACTION:BUILD|BROWSE|RESEARCH|CHECK_WEATHER|PROMPT_PROJECT|ADD_TASK|REMEMBER|OPEN_PROJECT]`, `[DISPATCH_TO_AGENT]`); fast-path intent regexes; native research via Opus web tools; design partner (`design_partner.py`); weather pipeline (`weather.py`, Open-Meteo); SQLite + FTS5 memory (`memory.py`); process event bus (`process_events.py`); Calendar / Mail (RO) / Notes via AppleScript; Playwright browser (`browser.py`); persistent Claude Code sessions (`work_mode.py`); greenfield scaffolds (`actions.py`).
- **Infrastructure:** supervised PR factory (`.github/workflows/factory.yml`, two required checks, merge policy in `CLAUDE.md`); macOS launchd service (`scripts/com.valet.backend.plist`, `scripts/valet-launchd.sh`); local SSL self-signed certs for Web Speech API; screenshots via FastAPI StaticFiles; smoke test (`scripts/smoke_test.sh`).

---

## Tech stack  [rewrite]

| Layer | Technology | Notes |
|---|---|---|
| Backend (app) | FastAPI + Python 3.11+ | `server.py` (voice-dispatch core kept here; low-risk concerns split out) |
| Frontend (app) | Vite 6 + TypeScript 5.7 + Three.js 0.183 | `frontend/` |
| Communication | WebSocket (JSON + binary audio) | WSS over self-signed certs |
| Proxy / marketing site | Next.js on Vercel | `product-site/` (https://jarvis-y.vercel.app) |
| AI (fast) | Claude Haiku 4.5 (via proxy) | conversation; $1 / $5 per MTok |
| AI (deep) | Claude Opus 4.8 (via proxy) | research; $5 / $25 per MTok |
| TTS | Fish Audio (via proxy) | VALET voice — two British models (Male / Female) |
| STT | Chrome Web Speech API | client-side, free |
| Licensing DB | Supabase Postgres (Pro) | `licenses` + `license_usage` tables |
| Observability | Langfuse | license as user id, payloads scrubbed |
| Billing | Stripe | Pro $20/mo, Ultra $50/mo |
| Control | `action_executor.py` ABC + AppleScript backend | portable; Windows/Linux = swap |
| Memory | SQLite + FTS5 | `data/` |
| macOS integrations | AppleScript | Calendar, Mail (RO), Notes |
| Geocode + Weather | Open-Meteo | free, no API key |
| Packaging (planned) | Tauri + PyInstaller | Stage F — not yet built |

---

## Integrations & MCPs  [rewrite]

| Integration | Purpose | Cost | Status |
|---|---|---|---|
| Anthropic Claude API | Haiku 4.5 conversation, Opus 4.8 research — via the proxy | Haiku $1/$5, Opus $5/$25 per MTok | live (server-side keys) |
| Fish Audio TTS | VALET-voiced replies (two British voices) — via the proxy | ~$15 / 1M chars | live (server-side keys) |
| Supabase | license + usage store (`licenses`, `license_usage`) | Pro plan | live |
| Langfuse | proxy tracing, license as user id, payloads scrubbed | unknown | live |
| Stripe | checkout + subscriptions (Pro $20/mo, Ultra $50/mo) | per-transaction | live |
| Vercel | hosts `product-site/` (marketing + proxy) | unknown | live |
| Open-Meteo | geocoding + weather | free | live |
| Apple Calendar / Mail / Notes | local read via AppleScript (Mail read-only) | free | live |
| Playwright (Chromium) | web automation | free | live |
| Claude Code CLI | dispatched sub-agent tasks | inherits Claude plan | live |
| Cursor | paste target for design-panel ship handoff | external app | live |

*Source: no MCP config files found in repo (`.mcp.json` / `mcp.config.*` / `claude_desktop_config.json` absent). Integrations derived from `product-site/lib/proxy/*.ts`, `licensing.py`, `server.py`, `requirements.txt`, and `.env.example`.*

---

## Decisions log  [append-only — never rewrite or delete]

- **2026-06-10 — Stage F packaging will use Tauri, not Electron** — Tauri for a lighter footprint; PyInstaller bundles the FastAPI backend, and the built frontend ships inside the same signed, notarized macOS app.
- **2026-06-10 — Self-editing cut from shipped builds** — `self_mod` is dev-only. Access routes through `_load_self_mod()`, which returns `None` when shipped (`VALET_SHIPPED` or no `.git`), so `self_mod.py` is excludable from the bundle. (`restart_self` still lives in `self_mod` and must be relocated for packaged restart — Stage F.)
- **2026-06-10 — Two British voices, persona unchanged** — Only the Fish TTS model swaps (Male / Female); the butler persona ("Vee") is untouched. `_active_voice_id()` reads live so a settings change applies on the next reply.
- **2026-06-10 — Risk-tiered safety with confirm-first deletes** — Tier 0 (reads/lists/opens/navigate) runs automatically; Tier 1 (delete/move/overwrite, drive apps, keystrokes, scripts, bulk, protected paths) confirms first. Deletes go to Trash, never unlink. Always-available kill switch. New-file write is Tier 0; overwrite is Tier 1.
- **2026-06-10 — Control on a portable ActionExecutor; AppleScript-only for v1** — `ActionExecutor` ABC + `Capability` enum with a macOS AppleScript backend. Non-scriptable apps return a structured `not_supported` (detected via `.sdef`/Info.plist without launching). A Windows/Linux or Accessibility/vision backend becomes a swap, not a rewrite.
- **2026-06-10 — App speaks the native Anthropic Messages dialect** — Repointing to the proxy is a base-URL + header swap (SDK `base_url` → `/api/proxy`, license in `X-License-Key`). Avoids a bespoke proxy protocol and keeps the SDK intact.
- **2026-06-10 — Soft-warn fair use + 7-day offline grace** — The fair-use cap (default $8/mo) warns but never blocks at launch; the license check tolerates a 7-day offline grace window so transient network failure doesn't brick the app. Both are deliberately lenient for launch and must tighten before scale.
- **2026-06-10 — Bundled + metered AI, no user API key** — The product bills centrally through the hosted proxy; the download ships with no vendor secrets. Rejected: making each user supply their own Anthropic/Fish key (worse UX, no metering, no entitlement control).
- **2026-06-10 — Proxy lives in product-site (one Vercel deploy)** — The license-gated proxy is co-located with the marketing/checkout site rather than a separate service, so there's a single Vercel deploy and one place the vendor keys live.
- **2026-06-10 — Leave the voice-dispatch core in server.py** — Stage B2 extracted only cleanly-decoupled concerns (`task_manager.py`, `voice_text.py`, `project_scanner.py`). Splitting `voice_handler` + the `_execute_*` handlers would be a fake boundary with high regression risk for no gain.
- **2026-06-10 — Retire JARVIS / Marvel branding → VALET (Vee)** — Renamed the assistant everywhere in the running product (persona, wake words, logger namespaces `valet.*`, launchd label `com.valet.backend`, log filenames, marketing site, README). Preserved deliberately: the `jarvis-y` GitHub remote/Vercel URL and historical docs (`OVERNIGHT_*.md`, `docs/*_diagnosis.md`).
- **2026-06-10 — Supervised PR factory before the major-changes wave** — Two required checks, fail-closed reviewer, branch protection; auto-merge OFF (`FACTORY_AUTOMERGE` unset) until a ~2-week soak.
- **2026-06-04 — Target Cursor's terminal by AXDescription, not AXRole** — The focused terminal element is an `AXTextField` whose AXDescription contains "terminal"; AXRole can't discriminate it from the palette/editor.
- **2026-06-04 — Verify the Claude pane is live before pasting, else file-ship** — Classify each Cursor terminal pty by its foreground `ps` process; only paste into a confirmed live `claude` pane, otherwise fall back to a staged file ship.
- **2026-06-03 — Hard-cap warm context at 6000 chars** — An oversized (~44KB) clipboard paste raced the Enter key and never landed; `project_context.py` now truncates composed context with a visible marker.
- **2026-05-22 — Dedicated weather pipeline instead of generic research** — `[ACTION:CHECK_WEATHER]` + Open-Meteo (free, structured, no API key) replaced routing through `[ACTION:RESEARCH]`.
- **2026-05-21 — Greenfield projects scaffold before first prompt** — `new_cursor_project` runs an initial commit so the clean-tree gate doesn't block the first ship.
- **2026-05-21 — Strip em-dashes from LLM output at runtime** — Em-dashes were an LLM tell; runtime responses strip them. (Persona rule: hard no-em-dash.)
- **2026-05-20 — Design-mode requires explicit opt-in** — Design mode requires an explicit phrase rather than inferring from intent.
- **2026-05-17 — Native research via Opus web tools** — Replaced folder-based research output with native Opus web tools and streaming source-preview cards.
- **Architectural — Mail is read-only by design** — Sending mail is intentionally out of scope.
- **Architectural — AppleScript over OAuth** — Calendar/Mail/Notes use native AppleScript; no token management. Google Calendar is the one OAuth exception.

---

## Open loops  [rewrite]

- [ ] **Stage F — Tauri packaging:** bundle FastAPI via PyInstaller + the built frontend into one signed, notarized macOS app that passes Gatekeeper — owner: Finley
- [ ] **Stage F — first-run permission onboarding:** Full Disk Access, per-app Automation, Accessibility — owner: Finley
- [ ] **Stage F — wire the signed artifact into `product-site` `/api/download`** (one-line swap of `DOWNLOAD_SOURCE`) — owner: Finley
- [ ] **Stage F — error reporting** (Sentry-style) with PII scrubbing + a telemetry consent toggle + a privacy policy — owner: Finley
- [ ] **Relocate `restart_self` out of `self_mod`** so packaged restart works after self_mod is excluded — owner: Finley
- [ ] **Close the personal-keys / soft-cap risk** before real distribution — owner: Finley (see Risks)
- [ ] Decide the exact fair-use number (currently $8/mo soft) — owner: Finley
- [ ] Decide Sentry vs alternative for error reporting — owner: Finley
- [ ] Determine the Apple Developer signing certs needed for notarization — owner: Finley
- [ ] Pick the public product name + domain (name = VALET; domain TBD) — owner: Finley
- [ ] Add `CLAUDE_CODE_OAUTH_TOKEN` secret to `jarvis-y` so factory-review can authenticate — owner: Finley
- [ ] After the ~2-week factory soak, decide whether to flip `FACTORY_AUTOMERGE` on — owner: Finley

---

## Risks & known issues  [rewrite]

- **TOP RISK — shipped users bill the owner's personal keys with only a soft cap.** The bundled model routes every shipped user's AI + TTS through the proxy's single Anthropic + Fish keys (the owner's personal keys). The fair-use cap ($8/mo default) only soft-warns — it never blocks — and is unvalidated. This must be closed (per-license hard limits or per-customer cost accounting) before any real distribution.
- **Soft fair-use + 7-day offline grace are intentionally lenient for launch.** A canceled/abused license keeps working for up to 7 days offline; over-allowance never stops a request. Fine for a controlled launch, not for scale.
- **`restart_self` still lives in `self_mod`**, which is excluded from shipped builds — packaged restart will break until it's relocated (Stage F dependency).
- **No signed/notarized build exists yet.** `product-site` `/api/download` serves a `0.0.1-placeholder`; Gatekeeper/permissions onboarding is unbuilt (Stage F).
- **Factory not yet functional end-to-end** — factory-review needs `CLAUDE_CODE_OAUTH_TOKEN` (repo has zero secrets) before it can pass.
- **Self-mod ship path** is sensitive to the Cursor build's accessibility tree and integrated-terminal `ps` layout (dev-only now, but still the dev workflow).
- **Single-vendor TTS** — Fish Audio has no fallback configured.
- **Self-signed certs** require manual Chrome trust on first run; documented only as the openssl command.
- **Anthropic 529s** during the design turn previously forced an emergency model swap; no resilience layer added since.

---

## Links  [rewrite]

- **Live URL (proxy + marketing):** https://jarvis-y.vercel.app
- **App:** local-only (`http://localhost:5173`)
- **GitHub:** https://github.com/Kuba-Ventures/jarvis-y (remote/URL names deliberately NOT renamed; org is Kuba-Ventures; local dir is `~/Code/VALET`)
- **Staging:** n/a
- **Client Drive folder:** n/a
- **Slack channel:** n/a
- **Related repos:** `product-site/` (the Next.js marketing + proxy site, in this repo); Kuba-Ventures soultech / Dharma (the PR-factory pattern this repo mirrors)

---

## Changelog  [append-only — never rewrite or delete]

- **2026-06-10:** kuba-vault full rewrite — VALET (Vee) rename across PROJECT.md, zero JARVIS references. Captured the Phase 2 re-scaffold: Stage A proxy spine (live, `product-site/`), Stage B app repoint + license gate (`licensing.py`, 7-day grace), Stage B2 module extraction (`task_manager`/`voice_text`/`project_scanner`), Stage C portable `ActionExecutor` + AppleScript backend, Stage D risk-tiered safety (`safety.py`/`safe_executor.py`, kill switch, confirm cards), Stage E two British voices + self_mod cut from shipped builds (PR #17). Recorded integrations + costs, locked decisions, and Stage F as the only remaining work. Flagged the personal-keys / soft-cap top risk.
- **2026-06-10:** Stage E (PR #17) merged — two-voice picker (British Male/Female), `_active_voice_id()` live read; self_mod disabled + excludable in shipped builds via `_load_self_mod()`.
- **2026-06-10:** Stage D merged (PRs #15, #16) — risk-tiered safety engine (`safety.py`, `safe_executor.py`), kill switch, confirm-first Trash-safe deletes, frontend confirm card + STOP button, `/api/safety/*` routes.
- **2026-06-10:** Stage C merged (PR #12) — `action_executor.py` ABC + `Capability` enum + `applescript_executor.py` macOS backend; portable control layer.
- **2026-06-10:** JARVIS → VALET rename merged (PR #14) — persona, wake words, logger namespaces, launchd label, marketing site, README; Marvel/MCU framing retired. Local dir now `~/Code/VALET`; `jarvis-y` remote/URL preserved.
- **2026-06-10:** Stage B merged (PRs #10, #11) — app routes AI + TTS through the proxy with a license gate (`licensing.py`); in-app API-key entry removed; `task_manager.py`/`voice_text.py`/`project_scanner.py` extracted from `server.py`.
- **2026-06-10:** Stage A merged (PR #9) — license-gated AI/TTS proxy in `product-site/` (live on Vercel), Supabase `license_usage` + `record_usage()` RPC, soft fair-use metering, Langfuse tracing.
- **2026-06-10:** Supervised PR factory wired up — `factory.yml` (factory-tests + fail-closed factory-review), merge policy in `CLAUDE.md`, branch protection on main. Auto-merge OFF until a ~2-week soak.
- **2026-06-04:** Self-mod ship hardened — focus Cursor terminal by AXDescription, verify the Claude pane is live before paste (else file-ship), `abandon_feature_branch()` + scrap-branch path.
- **2026-06-03:** Render-only multi-day weather panel; ship-phrase fix; repo-rename cleanup.
- **2026-05-31:** Fixed self-mod silently dropping a ~44KB paste — `project_context.py` hard-caps warm context at 6000 chars.
- **2026-05-22:** kuba-vault initial PROJECT.md superdoc — scanned repo, reconciled README/CLAUDE.md, summarized chunks 0-33.
- **2026-05-22:** Native `[ACTION:CHECK_WEATHER]` via Open-Meteo, floating weather card.
- **2026-05-21:** Greenfield projects (design + scaffold + ship); `_looks_like_app()` routing fix; strip em-dashes at runtime.
- **2026-05-19:** Voice → Claude Code via auto-paste (Mode 1) and dictation (Mode 2).
- **2026-05-17:** Native Opus research, streaming source-preview cards, floating result panels; baseline phases 1-5.
