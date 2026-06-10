# JARVIS
*Voice-first macOS assistant being turned into a sellable, downloadable product.*

*Last updated: 2026-06-10 16:50 ET by kuba-vault*

---

## TL;DR

JARVIS is a local, voice-first macOS assistant — British-butler persona, audio-reactive Three.js orb, Claude under the hood, Fish Audio for TTS, AppleScript into Calendar/Mail/Notes, Playwright for the web, and a self-mod path that ships prompts into Cursor/Claude Code. Today (2026-06-10) we landed the bulk of a **Phase 2 re-scaffold** that converts the prototype into a product you can buy and download: a license-gated AI/TTS proxy hosted on the existing marketing site (`product-site/`), an app that no longer ships any vendor API keys (it carries a license key and routes all AI/TTS through the proxy), per-license usage metering with a soft-warn fair-use cap, and a new portable control layer (`ActionExecutor`) that decouples Mac control from AppleScript. Stage A (proxy) and Stage B1/B2 (app repoint + license gate + `server.py` split) are merged; Stage C (control layer) is in review as PR #12. Remaining: D (risk-tiered safety + confirmations), E (personas/voices + cut `self_mod`), F (Tauri/Electron packaging + signed download + error reporting). Self-directed; the bundled-AI model currently bills the owner's personal Anthropic + Fish keys, capped by fair-use metering.

---

## What it is

**The problem:** Talking to LLM chat boxes is slow, and a normal user can't be expected to get an Anthropic key, a Fish key, generate SSL certs, and run two dev servers just to talk to an assistant.
**The solution:** A single voice loop on the Mac that routes intent (conversation, system lookup, research, design, ship-to-Cursor) — packaged so a buyer pays one subscription, downloads an app, pastes a license key, and talks. No API keys, no setup. AI + TTS usage is bundled and metered behind a hosted proxy.
**The user:** Two audiences now — (1) the original solo builder who lives in Claude Code/Cursor/the Apple suite, and (2) a paying end user who just wants a capable voice assistant without the plumbing.
**The value:** Hands-free orchestration of the dev/personal stack with sub-second voice latency for the simple stuff and full Opus + web tools for the hard stuff — sold as a one-subscription download.

---

## Status

- **Phase:** post-MVP iteration → **Phase 2 re-scaffold ("prototype → sellable product")**, mid-flight
- **Engagement manager:** self-directed
- **Lead:** Finley
- **Cadence:** continuous (per-feature commits / PRs through the supervised factory)
- **Next milestone:** land Stage C (PR #12), then Stage D (risk-tiered safety + confirm card + kill switch)
- **Flags:** shipping

---

## Where we are right now

Phase 2 turns the local prototype into a downloadable product where the user brings no API key and pays one subscription. Today four stages landed. **Stage A** (merged PR #9, live in prod) added a license-gated AI/TTS proxy inside `product-site/` — it holds the Anthropic + Fish keys server-side, validates every call against the Supabase `licenses` table, meters per-license usage against a soft-warn fair-use cap, and traces everything to Langfuse. **Stage B1** (merged PR #10, live) repointed `server.py` at that proxy (Anthropic SDK `base_url` + `X-License-Key`; TTS via `/api/proxy/tts`) and added `licensing.py`, a license gate with a 7-day offline grace window; the in-app API-key entry path is gone and the assistant refuses to answer when the license isn't entitled. **Stage B2** (merged PR #11) split the ~5,790-line `server.py` down to ~5,325 by extracting the cleanly-decoupled `task_manager.py`, `voice_text.py`, and `project_scanner.py`; the tightly-coupled voice-dispatch core was deliberately left intact. **Stage C** (PR #12, in review) adds a portable `ActionExecutor` interface + macOS `applescript_executor.py` backend — not yet wired into `server.py` callers (that's Stage D). Worth attention: every shipped user's AI/TTS currently bills the owner's personal Anthropic + Fish keys (fair-use cap is the only guard); the public product name/domain is still the `[JARVIS]`/`[PRODUCT_NAME]` placeholder; and `self_mod.py` is still in the build, slated to be cut in Stage E.

---

## What's built

**Product site / proxy (`product-site/`, Next.js on Vercel)**
- Marketing + checkout site: hero, capabilities, 3-tier pricing (Free / Pro $20 / Ultra $50), FAQ, Stripe checkout, success page, GTM conversion events, orb favicon
- **License-gated AI/TTS proxy** (Stage A, live): routes `app/api/proxy/{completion,research,tts,usage}` plus a native `app/api/proxy/v1/messages` route; logic in `lib/proxy/{auth,pricing,usage,langfuse,anthropic,fish}.ts`
- Proxy holds the Anthropic + Fish Audio keys server-side — the downloadable app ships with **no** vendor secrets
- Per-call license validation against the Supabase `licenses` table (`lib/proxy/auth.ts`); per-license metering of requests / tokens / est. cost against a fair-use allowance with **soft-warn** behavior (never blocks at launch)
- Langfuse trace per call, keyed by license as user id, payloads scrubbed by default
- `supabase/migration_usage.sql`: `license_usage` table + atomic `record_usage()` RPC (30-day rolling reset)
- License issuance/validation: `app/api/license/validate`, `app/api/stripe/webhook`, `lib/license.ts`; download endpoint `app/api/download`
- Smoke test: `scripts/smoke-proxy.sh`. Zero new npm deps for the proxy.

**App — frontend / UI**
- Three.js particle orb that deforms to TTS audio (`frontend/src/orb.ts`)
- Live "process panel" — draggable, holographic, auto-show/auto-dismiss event feed (`frontend/src/processPanel.{ts,css}`)
- Design panel with target dropdown incl. "+ New project…" inline name+stack flow (`frontend/src/designPanel.{ts,css}`)
- Floating result cards: research sources, product cards, web fetches, weather (`frontend/src/floatingPanels.ts`)
- Settings UI now has **License Key + Proxy URL** fields (the old per-vendor API-key entry was removed; `test-anthropic`/`test-fish` replaced by `test-license`) (`frontend/src/settings.ts`)
- Wake-phrase detection ("ok jarvis", "hey jarvis") via Web Speech API

**App — backend / data**
- FastAPI server, WebSocket loop (`server.py`, ~5,325 lines after the Stage B2 split)
- **All AI + TTS now routes through the proxy**: Anthropic SDK `base_url` → proxy with `X-License-Key`; `synthesize_speech` posts to `/api/proxy/tts`
- **`licensing.py`** — validates `LICENSE_KEY` against `{PROXY_BASE_URL}/api/license/validate`, 7-day offline grace, state persisted to `data/license_state.json`; assistant loop refuses when not entitled. Dev fallback (no `LICENSE_KEY` → direct keys) preserved for the internal repo only.
- **Portable control layer (Stage C, PR #12):** `action_executor.py` (`ActionExecutor` ABC, `ActionResult`, `Capability` enum) + `applescript_executor.py` (macOS backend). Capabilities: open app/path, run app command, read/write/move/delete file (delete → Trash), list folder, send keystroke, navigate, run script. Detects app scriptability from the bundle's `.sdef`/Info.plist without launching the app; non-scriptable apps return a structured, logged `not_supported` result. Not yet wired into `server.py` callers.
- Extracted modules (Stage B2): `task_manager.py`, `voice_text.py`, `project_scanner.py`
- Action system with tag-driven dispatch (`[ACTION:BUILD/BROWSE/RESEARCH/CHECK_WEATHER/...]`, `[DISPATCH_TO_AGENT]`)
- Native research via Claude Opus web tools; design partner (`design_partner.py`); self-mod machinery (`self_mod.py`, slated for removal in Stage E)
- Weather pipeline (`weather.py`), memory (`memory.py`, SQLite + FTS5), process event bus (`process_events.py`)
- Calendar / Mail (read-only) / Notes via AppleScript; Playwright browser; persistent Claude Code sessions (`work_mode.py`)

**Infrastructure**
- Supervised PR factory: `.github/workflows/factory.yml` (factory-tests + fail-closed factory-review), merge policy in `CLAUDE.md`, `pr-reviewer` subagent. Auto-merge OFF behind repo var `FACTORY_AUTOMERGE` until a ~2-week soak (Phase 3).
- macOS launchd auto-start; local SSL via self-signed certs (Web Speech API needs HTTPS)
- Screenshots served via FastAPI StaticFiles at `/screenshots/`

---

## Tech stack

| Layer | Technology | Notes |
|---|---|---|
| App backend | FastAPI + Python 3.11+ | `server.py` (~5,325 lines) + extracted modules |
| App frontend | Vite 6 + TypeScript 5.7 + Three.js 0.183 | `frontend/` |
| Product site / proxy | Next.js (App Router) + Tailwind | `product-site/`, deployed on Vercel |
| Communication | WebSocket (JSON + binary audio) | WSS over self-signed certs |
| AI (fast) | Claude Haiku 4.5 ($1 / $5 per MTok) | conversation, via proxy |
| AI (deep) | Claude Opus 4.8 ($5 / $25 per MTok) | research / design, via proxy |
| TTS | Fish Audio (~$15 / 1M chars) | via proxy; voice model `612b878b113047d9a770c069c8b4fdfe` |
| STT | Chrome Web Speech API | client-side, free |
| Licensing | `licenses` table (Supabase) + `licensing.py` | 7-day offline grace |
| Metering | `license_usage` table + `record_usage()` RPC | 30-day rolling reset |
| Observability | Langfuse | per-call trace, license = user id |
| Payments | Stripe | $20/mo Pro + $50/mo Ultra price IDs configured |
| Database | Supabase Postgres (project `jarvis` / `ufqvgujnphaejewqmugg`) | |
| Memory | SQLite + FTS5 | `data/jarvis.db` |
| Browser automation | Playwright | `browser.py` |
| macOS integrations | AppleScript + Swift helper / `applescript_executor.py` | Calendar, Mail (RO), Notes, Terminal |
| Geocode + Weather | Open-Meteo | free, no API key |
| Hosting | App: local on the user's Mac. Proxy + site: Vercel | |
| CI / PR factory | GitHub Actions + `anthropics/claude-code-action` + pytest | `.github/workflows/factory.yml` |

---

## Integrations & MCPs

| Integration | Purpose | Cost | Status |
|---|---|---|---|
| Anthropic Claude API | Haiku 4.5 conversation, Opus 4.8 research/design — server-side behind the proxy | usage-based ($1/$5 Haiku, $5/$25 Opus per MTok) | live |
| Fish Audio TTS | JARVIS-voiced spoken responses, server-side behind the proxy | metered (~$15 / 1M chars) | live |
| Supabase | `licenses` + `license_usage` tables, `record_usage()` RPC, auth/data | Pro plan | live |
| Langfuse | per-call observability/tracing (license = user id, payloads scrubbed) | observability tier | live |
| Stripe | subscriptions — $20/mo Pro, $50/mo Ultra | per-transaction fees | live |
| Vercel | hosts `product-site` + the AI/TTS proxy | hosting tier | live |
| Open-Meteo | geocoding + weather forecasts | free | live |
| Apple Calendar / Mail / Notes | local read via AppleScript (Mail read-only by design) | free | live |
| Playwright (Chromium) | web automation for browse / og:image enrichment | free | live |
| Claude Code CLI | dispatched sub-agent tasks via `claude -p` (internal/dev) | usage-based | live (dev) |
| Cursor | paste target for design-panel ship handoff (internal/dev) | external app | live (dev) |

**Bundled-AI cost note:** because the app ships no keys, all users' AI + TTS currently bills the owner's *personal* Anthropic + Fish accounts. The fair-use cap (`FAIR_USE_MONTHLY_USD`, default $8/mo, soft-warn / never blocks) plus per-license metering are the only guards today. Move to an org billing account before scale.

*Source: no MCP config files found in repo. Integrations read from `product-site/lib/proxy/*.ts`, `licensing.py`, `requirements.txt`, `.env.example`, and the git history.*

---

## Decisions log

- **2026-06-10 — Bundled + metered AI, no user API key** — The product ships zero vendor secrets; the user pays one subscription and all AI/TTS routes through a hosted proxy that holds the keys and meters usage. Rejected: making each buyer bring/manage their own Anthropic + Fish keys (kills the "download and talk" value prop). Accepted cost risk: all usage bills the owner's personal accounts until an org account is set up; mitigated by fair-use metering.
- **2026-06-10 — Proxy lives in the existing `product-site` (one repo, one deploy)** — The AI/TTS proxy is a set of Next.js routes inside the marketing/checkout site rather than a standalone service. One Vercel deploy, the keys already live where Stripe/Supabase do, zero new infra. Rejected: a separate proxy service (more ops surface for no near-term gain).
- **2026-06-10 — App speaks the native Anthropic Messages dialect** — Because the proxy exposes a native `/v1/messages` route, repointing the app was just a `base_url` + `X-License-Key` header swap on the existing Anthropic SDK — no client rewrite. Keeps the app's LLM code idiomatic and future SDK upgrades cheap.
- **2026-06-10 — Fair-use is soft-warn at launch** — Metering warns past the allowance (`FAIR_USE_MONTHLY_USD`, default $8/mo) but never blocks a call at launch, to avoid cutting off early paying users over a number we haven't tuned. Hard enforcement is deferred until the allowance is validated against real usage.
- **2026-06-10 — 7-day offline grace for license validation** — `licensing.py` keeps the app working for 7 days after a successful validation through transient network failures, but a key that validates as canceled/past_due/invalid disables the assistant loop immediately. Balances offline resilience against revocation.
- **2026-06-10 — Stop the `server.py` split at the decoupled modules** — Extracted `task_manager.py`, `voice_text.py`, `project_scanner.py` (clean boundaries) but deliberately left the voice-dispatch loop + `_execute_*` handlers in `server.py`. Splitting that tightly-coupled core would create a fake module boundary with high regression risk for no architectural gain.
- **2026-06-10 — Risk tiering is NOT in the control layer (Stage C)** — `ActionExecutor` only exposes capabilities and reports `not_supported` (structured + logged) for non-scriptable apps. Confirmations, Tier 0/1 risk gating, and the kill switch are Stage D, which *wraps* the executor — keeping the portable interface clean and OS-independent.
- **2026-06-10 — Deletes go to Trash, never permanent erase** — Backends must send `delete_file` to Trash. A destructive action should always be recoverable.
- **2026-06-10 — `self_mod.py` to be cut from the shipped build (Stage E)** — Self-modification stays for internal dev use but is removed from the distributable: a product that can rewrite its own source is the wrong trust surface for a paying end user.
- **2026-06-10 — Supervised PR factory before the major-changes wave** *(carried)* — Risky edits land through a governed pipeline (two required checks, fail-closed reviewer, branch protection). Auto-merge OFF until a ~2-week soak (Phase 3).
- **2026-06-04 — Target Cursor's terminal by AXDescription, not AXRole** — The integrated terminal's focused element is an `AXTextField`; AXDescription always contains "terminal", so it's the reliable signal.
- **2026-06-04 — Verify the Claude pane is live before pasting, else file-ship** — Classify every Cursor terminal pty by its foreground `ps` process; only paste into a confirmed live `claude`, otherwise stage a file ship.
- **2026-06-03 — Hard-cap warm context at 6000 chars** — A ~44KB clipboard paste raced the Enter key and never landed; `project_context.py` now truncates composed context with a visible marker.
- **2026-05-22 — Dedicated weather pipeline instead of generic research** — Replaced `[ACTION:RESEARCH]` weather routing with `[ACTION:CHECK_WEATHER]` + Open-Meteo.
- **2026-05-21 — Greenfield projects scaffold before first prompt** — `new_cursor_project` runs an initial commit so the clean-tree gate doesn't block the first ship.
- **2026-05-21 — Strip em-dashes from LLM output at runtime** — Em-dashes were an LLM tell; stripped from runtime responses.
- **2026-05-20 — Design-mode requires explicit opt-in (Option C)** — Design mode requires an explicit phrase rather than inferring from intent.
- **2026-05-19 — Auto-paste over file-default for ship handoff** — Mode 1 (auto-paste into target IDE) wins over Mode 2 (dictation).
- **2026-05-17 — Native research via Opus web tools, no scratch folders** — Replaced folder-based research output with native Opus web tools + streaming source cards.
- **Architectural — Mail is read-only by design**; **AppleScript over OAuth** (Google Calendar excepted).

---

## Open loops

- [ ] Land Stage C — review + merge PR #12 (`ActionExecutor` + AppleScript backend) — owner: Finley
- [ ] Stage D — risk-tiered safety wrapping the Stage C executor: Tier 0 (auto) vs Tier 1 (confirm-first), confirm card in the holographic panel, deletes-to-Trash, global kill switch — owner: Finley
- [ ] Stage E — selectable personas/voices + remove `self_mod.py` from the distributable — owner: Finley
- [ ] Stage F — Tauri-or-Electron packaging, signed + notarized download wired into `product-site` `/api/download`, error reporting (Sentry-style) with PII scrubbing + telemetry consent — owner: Finley
- [ ] **Decide:** final public product name + domain (currently `[PRODUCT_NAME]` / `[JARVIS]` placeholder) — owner: Finley
- [ ] **Decide:** exact fair-use allowance number (`FAIR_USE_MONTHLY_USD`, default $8/mo) — owner: Finley
- [ ] **Decide:** Tauri vs Electron for packaging — owner: Finley
- [ ] **Decide:** Sentry vs alternative for error reporting — owner: Finley
- [ ] Move bundled AI/TTS off the owner's personal Anthropic + Fish accounts to an org billing account before scale — owner: Finley
- [ ] After the ~2-week factory soak, decide whether to flip `FACTORY_AUTOMERGE` on (Phase 3) — owner: Finley
- [ ] Update README.md / CLAUDE.md line-count references (still say "~2300 lines") — owner: Finley
- [ ] Fix stale `.venv` `jarvis-main` shebang after the local repo rename — owner: Finley

---

## Risks & known issues

- **All users' AI/TTS bills the owner's personal Anthropic + Fish keys** — the bundled model means cost lands on personal accounts. Fair-use metering + the soft-warn cap are the only guards, and the cap is soft (never blocks) at launch. A runaway or abusive license can spend real money before anyone notices; an org billing account + the right cap are not yet in place.
- **Fair-use number is unvalidated** — `FAIR_USE_MONTHLY_USD` default ($8/mo) is a placeholder; too low frustrates buyers, too high invites cost. Needs real-usage tuning before hard enforcement.
- **Single-vendor AI and TTS** — both Anthropic and Fish Audio are single points of failure with no fallback; a proxy/vendor outage takes the whole product down for every licensed user.
- **License gate / offline grace edge cases** — a 7-day offline grace plus an entitlement check means revocation isn't instant offline, and a misconfigured `PROXY_BASE_URL` or `licenses` row could lock out (or over-grant) a paying user. Not yet load- or failure-tested.
- **Stage C executor is unwired** — `ActionExecutor` exists but no `server.py` caller uses it yet; the AppleScript-only v1 floor means non-scriptable apps are `not_supported` until later stages.
- **`self_mod.py` still in the build** — a self-rewriting code path remains shippable until Stage E removes it.
- **`server.py` size (~5,325 lines)** — still large and an always-escalate surface under the merge policy.
- **Self-signed certs** require manual Chrome trust on first run; not documented beyond the openssl command.
- **No app-side CI** — `tests/` outside the factory gate are run manually.
- **Stale `.venv` shebang** after the `jarvis-main` → `jarvis` local rename; console-script wrappers break until the venv is rebuilt.

---

## Links

- **Live URL (product site + proxy):** https://jarvis-y.vercel.app
- **App:** local-only (`http://localhost:5173`)
- **Supabase project:** `jarvis` (`ufqvgujnphaejewqmugg`)
- **GitHub:** https://github.com/Kuba-Ventures/jarvis-y (local dir `~/Code/jarvis`)
- **Staging:** n/a
- **Client Drive folder:** n/a
- **Slack channel:** n/a
- **Related repos:** Kuba-Ventures soultech / Dharma (the PR-factory pattern this repo mirrors)

---

## Changelog

- **2026-06-10:** Phase 2 re-scaffold (prototype → sellable product) — **Stage A** (PR #9, live): license-gated AI/TTS proxy in `product-site/` (`app/api/proxy/*` + native `/v1/messages`, `lib/proxy/*.ts`), holds Anthropic+Fish keys server-side, validates against Supabase `licenses`, per-license metering with soft-warn fair-use cap (`FAIR_USE_MONTHLY_USD` default $8), Langfuse tracing, `migration_usage.sql` (`license_usage` + `record_usage()` RPC). **Stage B1** (PR #10, live): `server.py` routes all AI/TTS through the proxy (base_url + `X-License-Key`, TTS via `/api/proxy/tts`); new `licensing.py` (7-day offline grace, `data/license_state.json`); in-app API-key entry removed, settings now License Key + Proxy URL; loop refuses when not entitled. **Stage B2** (PR #11): split `server.py` ~5,790 → ~5,325 via `task_manager.py`, `voice_text.py`, `project_scanner.py`. **Stage C** (PR #12, in review): portable `action_executor.py` + `applescript_executor.py` (capabilities incl. delete-to-Trash, scriptability detection via `.sdef`/Info.plist, structured `not_supported`). Decisions locked: bundled+metered AI, proxy-in-product-site, native Messages dialect, soft-warn fair-use, cut `self_mod` in Stage E. Open decisions: product name/domain, fair-use number, Tauri vs Electron, Sentry vs alt.
- **2026-06-10:** kuba-vault refresh — caught up self-mod ship reliability (AXDescription terminal focus, live-pane verify + file-ship fallback, 44KB paste fix), render-only multi-day weather panel; recorded the supervised PR factory (PR #1).
- **2026-06-10:** Supervised PR factory wired up — `factory.yml` (factory-tests + fail-closed factory-review), merge policy in CLAUDE.md, branch protection on main. Auto-merge OFF until a ~2-week soak.
- **2026-06-04:** Self-mod ship hardened — AXDescription terminal focus, verify Claude pane live before paste (else file-ship), `abandon_feature_branch()` + scrap-branch path.
- **2026-06-03:** Render-only multi-day weather panel; ship-phrase fix; repo-rename cleanup.
- **2026-05-31:** Fixed self-mod silently dropping a ~44KB paste — `project_context.py` caps warm context at 6000 chars.
- **2026-05-26:** kuba-vault refresh — no new commits since 2026-05-22; flagged quiet stretch.
- **2026-05-22:** kuba-vault initial PROJECT.md superdoc.
- **2026-05-22:** chunk 33 — native `[ACTION:CHECK_WEATHER]` via Open-Meteo + floating weather card.
- **2026-05-21:** chunk 32 — greenfield projects (design + scaffold + ship), stack picker.
- **2026-05-21:** chunks 26-31 — wake-phrase alias, sub-agent dispatch, em-dash strip, `_looks_like_app()` fix.
- **2026-05-19:** chunks 21-25 — voice → Claude Code via auto-paste + dictation, design-panel paste-target dropdown.
- **2026-05-17:** chunks 0-18 — process panel, haiku middleware, native Opus research + streaming source cards, floating result panels, self-modification machinery.
