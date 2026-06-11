# VALET
*Voice-Activated Local Engineering Terminal — a downloadable, license-gated macOS voice assistant.*

*Last updated: 2026-06-11 by kuba-vault*

---

## TL;DR  [rewrite]

VALET (formal name; answers to "Vee") is a local, voice-first macOS assistant with a British-butler persona, an audio-reactive Three.js orb, and a live process panel. The full commercial loop is now real: a buyer can purchase, get a license key, download a signed and notarized macOS app, install it with no Gatekeeper warning, and run it. Stage F (packaging) is done. This session shipped the signed/notarized DMG (Developer ID, Team QZX7VBLDZT, notarytool profile "valet-notary"), hosted as a public GitHub release on Kuba-Ventures/valet-downloads (tag v0.1.0, asset VALET_0.1.0_aarch64.dmg) with Vercel `DOWNLOAD_URL` pointed at it. It also added a comprehensive first-run onboarding wizard (license, permissions, voice, profile, connections) that re-runs on every new build and every fresh install, privacy-respecting action analytics into Langfuse (action tags only, no raw prompts/responses), a cinematic 5-page landing plus /privacy and /terms, and a `--clean` PyInstaller build fix so releases never bundle stale code. Stripe checkout is validated end to end, but in SANDBOX/test mode only. Top open risk is unchanged: every shipped user bills the owner's personal Anthropic + Fish keys, gated only by a soft, unvalidated fair-use cap that warns but never blocks. Close that before real distribution.

---

## What it is  [rewrite when value prop evolves]

**The problem:** Talking to LLM chat boxes is slow, and stitching together Calendar, Mail, a browser, terminals and a coding agent to get one thing done is slower. Shipping that as a product also means no end user should have to paste in their own API keys.
**The solution:** A single voice loop on the Mac that routes intent — conversation, system lookup, research, file/app control, ship-to-Cursor — without leaving the orb, with all AI billed centrally behind a license so the download ships with zero vendor secrets.
**The user:** A macOS builder who lives in Claude Code, Cursor and the Apple suite and wants hands-free orchestration — and, post-Phase-2, a paying customer who buys a license and downloads a signed app.
**The value:** Sub-second voice for the simple stuff, full Opus + web tools for the hard stuff, native macOS control with a safety net — and a clean install with no key management.

---

## Status  [rewrite]

- **Phase:** launch prep — full buy/download/install/run loop is real on a signed, notarized build; remaining work is hardening billing and going live on payments.
- **Engagement manager:** self-directed
- **Lead:** Finley
- **Cadence:** continuous (per-PR through the supervised factory)
- **Next milestone:** turn the shared-key soft cap into per-license hard limits (or per-customer cost accounting), then flip Stripe to live mode. No hard date.
- **Flags:** shipping

---

## Where we are right now  [rewrite]

The product now ships. There is a signed and notarized macOS DMG (Developer ID, Team QZX7VBLDZT, notarized via the `valet-notary` notarytool profile) published as a public GitHub release on `Kuba-Ventures/valet-downloads` (tag `v0.1.0`, asset `VALET_0.1.0_aarch64.dmg`), and Vercel `DOWNLOAD_URL` points the proxy's `/api/download` redirect at it. The buy to license-key to download to clean-install (no Gatekeeper warning) to run loop has been exercised end to end. This session also landed a comprehensive first-run onboarding wizard (welcome, license activation, mic/computer-control/accessibility/full-disk permissions, voice, profile, connections) that re-runs on every new build and, via the `.app` creation-time stamp, on every fresh install (even re-downloading the same build). Two build/restart reliability fixes shipped: `build-macos.sh` now passes PyInstaller `--clean` so a release can never bundle stale `server.py` or frontend (an earlier incremental build had shipped the wizard-less app), and the parent watchdog now polls every 0.5s while the frozen backend waits for `:8340` to free before binding, fixing an "Application Not Responding" hang on relaunch after a macOS permission toggle. Proxy-side, privacy-respecting action analytics now extract the assistant's `[ACTION:TYPE]` tags into Langfuse as trace tags + metadata (e.g. `open_app`, `app:Spotify`, `check_weather`) without storing any raw prompts or responses; sensitive targets (file paths on deletes/builds) are dropped, only app/project names are kept. The marketing site got a cinematic 5-page overhaul plus new `/privacy` and `/terms` pages, and Langfuse MCP is connected for analytics (usage dashboard planned, not built). What needs the human's attention before real distribution: the bundled model bills the owner's personal Anthropic + Fish keys for every shipped user behind only a soft fair-use cap, and Stripe is still in sandbox/test mode. See Risks.

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

### Stage F — Packaging, signing, distribution (DONE, LIVE)
- **Signed + notarized macOS app exists and is live.** `packaging/build-macos.sh` runs PyInstaller `--clean` to bundle the FastAPI backend, Tauri builds + signs the `.app` (Developer ID Application, Team `QZX7VBLDZT`), then `xcrun notarytool submit --keychain-profile valet-notary --wait` notarizes and staples. Installs with no Gatekeeper warning.
- **Distribution:** the DMG is a public GitHub release on `Kuba-Ventures/valet-downloads` (tag `v0.1.0`, asset `VALET_0.1.0_aarch64.dmg`). Vercel env `DOWNLOAD_URL` points at it; `product-site/app/api/download/route.ts` validates the license then redirects to that URL (placeholder fallback only when the env var is unset).
- **First-run onboarding wizard** (`frontend/src/onboarding.{ts,css}`): welcome → license activation → permissions (mic, computer control, accessibility, full-disk) → voice → profile (name / DOB / location) → connections. Non-blocking (steps are skippable). Re-runs on every new build, and on every fresh install via the `.app` creation-time stamp (so re-downloading the same build re-triggers it).
- **Build reliability:** `--clean` (PR #42) prevents shipping a stale cached `server.py`/frontend; the watchdog + `:8340` free-before-bind fix (PR #43) makes relaunch after a permission toggle reliable instead of hanging.

### Proxy analytics + checkout (THIS SESSION)
- **Privacy-respecting action analytics** (`product-site/lib/proxy/anthropic.ts`, `langfuse.ts`; PRs #39, #40). A streaming transform extracts `[ACTION:TYPE] target` tags from the assistant's reply and logs them to Langfuse as trace tags + metadata (bare type, e.g. `open_app`, plus `app:Spotify`-style target tags). No raw prompts or responses are stored (`PROXY_CAPTURE_PAYLOADS` off by default); sensitive targets (file paths on deletes/builds) are dropped, only app/project names kept.
- **Stripe checkout validated end to end in SANDBOX/test mode:** purchase → license issued → proxy entitles the call. Not yet on live payments.
- **Marketing site** (PR #38): cinematic 5-page landing — home (particle orb + 01-03 sequence visuals + live-demo terminal + capabilities), `/how-it-works`, `/pricing`, `/faq`, `/contact` — plus new `/privacy` and `/terms`. Langfuse MCP connected for analytics; usage dashboard planned, not built.

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
| Billing | Stripe | Free / Pro $20/mo / Ultra $50/mo — sandbox/test mode |
| Control | `action_executor.py` ABC + AppleScript backend | portable; Windows/Linux = swap |
| Memory | SQLite + FTS5 | `data/` |
| macOS integrations | AppleScript | Calendar, Mail (RO), Notes |
| Geocode + Weather | Open-Meteo | free, no API key |
| Packaging | Tauri + PyInstaller (`--clean`) | signed (Developer ID, Team QZX7VBLDZT) + notarized (`valet-notary`); `packaging/build-macos.sh` |
| Distribution | GitHub release | `Kuba-Ventures/valet-downloads` `v0.1.0` → Vercel `DOWNLOAD_URL` |

---

## Integrations & MCPs  [rewrite]

| Integration | Purpose | Cost | Status |
|---|---|---|---|
| Anthropic Claude API | Haiku 4.5 conversation, Opus 4.8 research — via the proxy | Haiku $1/$5, Opus $5/$25 per MTok | live (server-side keys) |
| Fish Audio TTS | VALET-voiced replies (two British voices) — via the proxy | ~$15 / 1M chars | live (server-side keys) |
| Supabase | license + usage store (`licenses`, `license_usage`) | Pro plan | live |
| Langfuse | proxy tracing + privacy-respecting action analytics (tags/metadata, no raw payloads) | unknown | live |
| Langfuse MCP | analytics access for a planned usage dashboard | unknown | connected (dashboard planned) |
| Stripe | checkout + subscriptions (Free / Pro $20/mo / Ultra $50/mo) | per-transaction | testing (sandbox/test mode) |
| Vercel | hosts `product-site/` (marketing + proxy); `DOWNLOAD_URL` → release DMG | unknown | live |
| GitHub Releases | hosts the signed/notarized DMG (`Kuba-Ventures/valet-downloads` `v0.1.0`) | free | live |
| Open-Meteo | geocoding + weather | free | live |
| Apple Calendar / Mail / Notes | local read via AppleScript (Mail read-only) | free | live |
| Playwright (Chromium) | web automation | free | live |
| Claude Code CLI | dispatched sub-agent tasks | inherits Claude plan | live |
| Cursor | paste target for design-panel ship handoff | external app | live |

*Source: no MCP config files found in repo (`.mcp.json` / `mcp.config.*` / `claude_desktop_config.json` absent). Integrations derived from `product-site/lib/proxy/*.ts`, `licensing.py`, `server.py`, `requirements.txt`, and `.env.example`.*

---

## Decisions log  [append-only — never rewrite or delete]

- **2026-06-11 — Distribute the signed DMG as a public GitHub release, gated by the proxy** — The notarized `VALET_0.1.0_aarch64.dmg` lives as a release on `Kuba-Ventures/valet-downloads`; `product-site` `/api/download` validates the license then redirects to it via the Vercel `DOWNLOAD_URL` env var. Swapping a future build is one env change. Rejected: serving the binary through Vercel directly (size, bandwidth).
- **2026-06-11 — Onboarding re-runs on every fresh install, not just every build** — Keyed off the `.app` creation-time stamp (PR #44) so a re-download of the same build still re-triggers setup, ensuring permissions/license are re-walked on a clean machine.
- **2026-06-11 — Action analytics log tags only, never raw conversation** — The proxy extracts `[ACTION:TYPE]` tags into Langfuse as trace tags + metadata and drops sensitive targets (file paths on deletes/builds), keeping only app/project names. Gives a "most-used actions" view without storing prompts/responses (`PROXY_CAPTURE_PAYLOADS` off by default).
- **2026-06-11 — PyInstaller `--clean` on every build** — An incremental build had shipped a stale `server.py` + frontend (the wizard-less app). `--clean` wipes the cache every time so a release can never bundle stale code, at the cost of slower builds.
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

- [ ] **Close the shared-key / soft-cap risk** before real distribution — per-license hard limits or per-customer cost accounting (currently $8/mo soft-warn, unvalidated, never blocks) — owner: Finley (see Risks)
- [ ] **Flip Stripe to live mode** (currently sandbox/test) — owner: Finley
- [ ] **Add a license-key recovery path** — the key is only shown on the post-purchase success page (copy button), not emailed; a lost key is unrecoverable today — owner: Finley
- [ ] **Wire the onboarding "connections" step** (Google Calendar / Gmail / MCP / other apps) — currently "Coming soon" placeholders — owner: Finley
- [ ] **Google Calendar / Gmail integration** (per-user OAuth + Google app verification) — deferred — owner: Finley
- [ ] Add a TTS fallback so a Fish Audio outage doesn't kill voice — owner: Finley
- [ ] Build the planned Langfuse usage dashboard (MCP connected) — owner: Finley
- [ ] Decide the exact fair-use number (currently $8/mo soft) — owner: Finley
- [ ] **Relocate `restart_self` out of `self_mod`** so packaged restart works after self_mod is excluded — owner: Finley
- [ ] Add `CLAUDE_CODE_OAUTH_TOKEN` secret to `jarvis-y` so factory-review can authenticate — owner: Finley
- [ ] After the ~2-week factory soak, decide whether to flip `FACTORY_AUTOMERGE` on — owner: Finley

---

## Risks & known issues  [rewrite]

- **TOP RISK — shipped users bill the owner's personal keys with only a soft cap.** Every shipped user's AI + TTS runs through the proxy's single owner-held Anthropic + Fish keys. The fair-use cap ($8/mo default) only soft-warns: it never blocks, and is unvalidated. With a signed build now live, anyone with a license can drive unbounded spend on the owner's keys. Must become per-license hard limits or per-customer cost accounting before any real distribution.
- **Stripe is still in sandbox/test mode.** Checkout is validated end to end in test mode but no live payment has been taken. Going live needs the live keys/prices wired and re-verified.
- **No license-key recovery path.** The key is shown only on the post-purchase success page (with a copy button); it is not emailed. A user who loses it there has no way to recover it. The success-page copy already flags this as an open question.
- **Google Calendar / Gmail integration is deferred.** Onboarding shows both as "Coming soon" placeholders; per-user OAuth and Google app verification are not built. The onboarding "connections" step (Google/Gmail/MCP/other apps) is placeholder UI, not wired.
- **Single-vendor TTS** — Fish Audio has no fallback; an outage takes voice down.
- **Soft fair-use + 7-day offline grace are intentionally lenient.** A canceled/abused license keeps working for up to 7 days offline; over-allowance never stops a request. Fine for a controlled launch, not for scale.
- **`restart_self` still lives in `self_mod`**, which is excluded from shipped builds — packaged restart relies on the watchdog relaunch; the in-app `restart_self` path stays broken until relocated.
- **Factory not yet functional end-to-end** — factory-review needs `CLAUDE_CODE_OAUTH_TOKEN` (repo has zero secrets) before it can pass.
- **Self-signed certs** require manual Chrome trust on first run; documented only as the openssl command.
- **Anthropic 529s** during the design turn previously forced an emergency model swap; no resilience layer added since.

---

## Roadmap / Deferred  [append-only — intentionally deferred for future builds]

*As of 2026-06-11. This captures what is deliberately put off so it survives across sessions.*

**Rides the NEXT signed app build (needs rebuild + re-sign + notarize):**
- **#44 (merged): re-run onboarding on EVERY install, not just every new build.** On `main`, NOT in the build currently being deployed.
- The "sir" persona pass (below) batches here too.

**Merged, awaiting config to take effect:**
- **License-key email on purchase (#46, merged).** The Stripe webhook emails the buyer their key. Graceful no-op until activated. Activation: create a Resend account, verify a sending DOMAIN you own (cannot verify `*.vercel.app`, so a real domain is needed, e.g. `valetvoice.app`), then set `RESEND_API_KEY` and `EMAIL_FROM` in Vercel and redeploy.

**Config still owned by the operator:**
- Stripe is still in SANDBOX/test mode. Flip to live keys + webhook + re-test before real sales.
- Resend (`RESEND_API_KEY` + `EMAIL_FROM`) for the license email above.

**Deferred features (not started):**
- **Google Calendar / Gmail:** per-user OAuth + Google app verification. Onboarding shows "Coming soon" placeholders. Real project.
- **User accounts / self-serve billing portal:** log in, see your key anytime, manage or cancel the subscription. The fuller version of license recovery. Real project.
- **"Sir" persona pass:** roughly 20 instances in the app system prompt instruct Vee to address the user as "sir" (the old assistant persona). A deliberate tone decision plus an app rebuild.
- **TTS fallback:** Fish Audio is the single vendor with no backup. Add a secondary provider path.
- **Universal app control via the macOS Accessibility API (AXUIElement):** drive any app's UI by reading its accessibility tree and sending synthetic input. Big bet, do it selectively (native apps first where the tree is rich, screenshot + vision fallback for Electron / weak trees, every click / type gated behind the existing confirmation + kill switch).
- **Langfuse usage dashboard:** the Langfuse MCP is connected; build the apps-opened / actions-done / friction widgets (needs live action-tag data flowing first).

**Top liability status (context, already handled):** shared-key spend is now BOUNDED via `FAIR_USE_MODE=throttle` + `FAIR_USE_MONTHLY_USD=10` in Vercel (per-license cap live).

---

## Links  [rewrite]

- **Live URL (proxy + marketing):** https://jarvis-y.vercel.app
- **App:** local-only (`http://localhost:5173`)
- **GitHub:** https://github.com/Kuba-Ventures/jarvis-y (remote/URL names deliberately NOT renamed; org is Kuba-Ventures; local dir is `~/Code/VALET`)
- **Staging:** n/a
- **Client Drive folder:** n/a
- **Slack channel:** n/a
- **Download (release DMG):** https://github.com/Kuba-Ventures/valet-downloads/releases/tag/v0.1.0 (`VALET_0.1.0_aarch64.dmg`, signed + notarized; Vercel `DOWNLOAD_URL` points here)
- **Related repos:** `Kuba-Ventures/valet-downloads` (public, hosts the signed DMG releases); `product-site/` (the Next.js marketing + proxy site, in this repo); Kuba-Ventures soultech / Dharma (the PR-factory pattern this repo mirrors)

---

## Changelog  [append-only — never rewrite or delete]

- **2026-06-11:** Added a "Roadmap / Deferred" section capturing intentionally-deferred work so it survives across sessions — next-build riders (onboarding-on-every-install #44), license-key email awaiting Resend config (#46), operator-owned config (Stripe live, Resend), not-started features (Google/Gmail, accounts/billing portal, "sir" persona pass, TTS fallback, Accessibility-API universal control, Langfuse dashboard). Noted shared-key spend is now BOUNDED (`FAIR_USE_MODE=throttle` + `FAIR_USE_MONTHLY_USD=10`).
- **2026-06-11:** kuba-vault refresh — Stage F is DONE: signed + notarized macOS DMG live as a `Kuba-Ventures/valet-downloads` `v0.1.0` release (Developer ID, Team QZX7VBLDZT, `valet-notary`), Vercel `DOWNLOAD_URL` wired; full buy → key → download → clean-install → run loop verified. Logged the onboarding wizard (PR #41) re-running per build and per fresh install (PR #44), PyInstaller `--clean` build fix (PR #42), reliable relaunch after a permission toggle (PR #43), privacy-respecting Langfuse action analytics (PRs #39/#40), and the 5-page landing + /privacy + /terms (PR #38). Phase → launch prep. Cleared the now-false "no signed build" risk; sharpened the open liabilities to: shared-key soft cap (top), Stripe still sandbox, no license-key recovery, deferred Google/Gmail, single-vendor TTS.
- **2026-06-11:** Onboarding now re-runs on every fresh install via the `.app` creation-time stamp (PR #44).
- **2026-06-11:** Reliable restart after a macOS permission toggle — watchdog polls every 0.5s, frozen backend waits for `:8340` to free before binding (PR #43).
- **2026-06-11:** `build-macos.sh` passes PyInstaller `--clean` so builds never bundle stale cache (PR #42).
- **2026-06-11:** Comprehensive first-run onboarding wizard — welcome, license, permissions, voice, profile, connections (PR #41).
- **2026-06-11:** Privacy-respecting action analytics into Langfuse (tags + metadata, no raw payloads), action tags split into type + `app:Name` (PRs #39, #40); new /privacy + /terms pages.
- **2026-06-11:** Cinematic 5-page landing overhaul — home, /how-it-works, /pricing, /faq, /contact (PR #38).
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
