# VALET
*Voice-Activated Local Engineering Terminal — a downloadable, license-gated macOS voice assistant.*

*Last updated: 2026-06-11 18:28 ET by kuba-vault*

---

## TL;DR  [rewrite]

VALET (formal name; answers to "Vee") is a local, voice-first macOS assistant with a British-butler persona, an audio-reactive Three.js orb, and a live process panel. The full commercial loop is real: buy, get a license key, download a signed and notarized macOS app, install with no Gatekeeper warning, run. This session moved Stripe from sandbox to **LIVE payments** (real `cs_live_` checkout sessions verified for Pro and Ultra; a live trial purchase issued a real key) and built a **full account system on valet-voice.com**: email + password auth via Supabase, a self-service customer dashboard (license key, plan, per-period usage, download, Stripe billing portal), an owner-only read-only admin view of all subscribers, and a desktop→web sync so the dashboard shows live profile, speech/activity stats and connected apps from the running app. Accounts and sync migrations have been run in production Supabase. The web side of all of this is merged; the desktop side of the sync (PR #57, `server.py` wiring `SuccessTracker` + a telemetry-gated push loop) is open and not yet merged. Caveat: Stripe **payouts are PAUSED** until a bank account is added — live charges land in the balance but can't pay out yet. Top open risk is unchanged: every shipped user bills the owner's personal Anthropic + Fish keys, gated only by a soft, unvalidated fair-use cap. Close that before real distribution.

---

## What it is  [rewrite when value prop evolves]

**The problem:** Talking to LLM chat boxes is slow, and stitching together Calendar, Mail, a browser, terminals and a coding agent to get one thing done is slower. Shipping that as a product also means no end user should have to paste in their own API keys.
**The solution:** A single voice loop on the Mac that routes intent — conversation, system lookup, research, file/app control, ship-to-Cursor — without leaving the orb, with all AI billed centrally behind a license so the download ships with zero vendor secrets.
**The user:** A macOS builder who lives in Claude Code, Cursor and the Apple suite and wants hands-free orchestration — and, post-Phase-2, a paying customer who buys a license and downloads a signed app.
**The value:** Sub-second voice for the simple stuff, full Opus + web tools for the hard stuff, native macOS control with a safety net — and a clean install with no key management.

---

## Status  [rewrite]

- **Phase:** launch prep — full buy/download/install/run loop is real on a signed, notarized build; Stripe is LIVE; remaining work is closing the shared-key billing risk and finishing payout setup.
- **Engagement manager:** self-directed
- **Lead:** Finley
- **Cadence:** continuous (per-PR through the supervised factory)
- **Next milestone:** turn the shared-key soft cap into per-license hard limits (or per-customer cost accounting), and add a Stripe bank account to resume payouts. No hard date.
- **Flags:** shipping

---

## Where we are right now  [rewrite]

This session did two big things: went **live on payments**, and built a **full account layer on valet-voice.com**. Stripe flipped from sandbox to LIVE under a new account "Twin Peaks Labs" — live keys, a live webhook at `https://www.valet-voice.com/api/stripe/webhook`, recreated Pro/Ultra products with new live price IDs, and the Customer Portal config saved in live mode. Verified live end to end via curl: both tiers create `cs_live_` checkout sessions, and a real trial purchase issued license `PRODUCT-HGE2-H65Z-78W6-Z7DC-RERS`. Caveat: **payouts are PAUSED** until a bank account is added — live charges land in the Stripe balance but can't pay out yet. On the product site, the identity model went from "just a license key in a header" to a real account system: email + password auth via Supabase (`@supabase/ssr`), a new `/account` section (login, signup, password reset, email verification enforced), a customer dashboard showing the license key, plan/tier/status, renew/trial date, per-period usage (reusing the existing `license_usage` metering), download link and a Stripe Billing Portal button. Accounts auto-claim their license by matching the buyer email, with a manual "link key" form for mismatches. An owner-only, read-only `/account/admin` view (gated on an `ADMIN_EMAILS` allow-list) shows summary cards (active, on-trial, est. MRR, period spend) and a subscriber table. A Stage-2 desktop→web sync landed on the web side (PR #56): an `account_sync` table, an authed `POST /api/proxy/sync` ingest endpoint with a strict allow-list sanitizer (no message content), and dashboard sections for Profile, Speech & activity, and Connected apps that degrade to a "waiting for your app" state before first sync. The desktop half of the sync (PR #57 — `server.py` wiring `SuccessTracker` into the live loop plus a telemetry-gated 15-min push of a profile/stats/connections snapshot) is **open, not yet merged**. The accounts and sync migrations have both been run in production Supabase. What needs the human's attention before real distribution is unchanged: the bundled model bills the owner's personal Anthropic + Fish keys for every shipped user behind only a soft fair-use cap. Plus near-term operator items: add a Stripe bank account, wire transactional email (Resend + custom SMTP in Supabase Auth), and add the www `/auth/callback` redirect in Supabase. See Risks.

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

### Self-service accounts + LIVE billing (THIS SESSION)
- **Stripe is LIVE.** Flipped from sandbox to live under a new account "Twin Peaks Labs": live secret/publishable keys, a live webhook at `https://www.valet-voice.com/api/stripe/webhook` (checkout.session.completed, customer.subscription.created/updated/deleted), recreated Pro/Ultra products with new live price IDs in Vercel (`STRIPE_PRICE_ID_PRO`/`STRIPE_PRICE_ID_ULTRA`), Customer Portal config saved in live mode. Verified live via curl — both tiers create `cs_live_` sessions; a real trial purchase issued `PRODUCT-HGE2-H65Z-78W6-Z7DC-RERS`. **Payouts PAUSED** until a bank account is added (activation incomplete).
- **Email + password auth** via Supabase Auth + `@supabase/ssr` (PRs #52, #53, #55). New `/account` section: login, signup, password reset, update-password, `/auth/callback` (code exchange). Email verification enforced (no session until confirmed; the license works in the app regardless). `middleware.ts` refreshes the session cookie and gates `/account/*`. Auth UI in `components/account/*` (`AuthForm`, `ResetForm`, `UpdatePasswordForm`, `PasswordInput` with eye-icon show/hide + confirm field, `SignOutButton`, `ManageBillingButton`, `ClaimLicenseForm`); Supabase clients in `lib/auth/{server,client}.ts`.
- **Account ↔ license linking:** `licenses` gained `customer_email` (from Stripe in `lib/license.ts`) and `user_id`. New accounts auto-claim a license by matching the buyer email on dashboard load; a manual "link key" form (`/api/account/claim`) covers a mismatched address. Data layer in `lib/account.ts` (`linkLicensesByEmail`, `claimLicenseByKey`, `getAccountLicenses`), all scoped to the verified session user via the service-role key.
- **Customer dashboard** (`app/account/page.tsx`): license key (copy), plan/tier, status, renew/trial date, per-period usage (voice requests, tokens, est. cost vs fair-use allowance — reuses `license_usage`), download link, **Manage billing** → Stripe Billing Portal (`/api/stripe/portal`). Success page now points buyers at `/account`; Nav gained an Account link.
- **Owner admin view** (`app/account/admin`, PR #54): owner-only, read-only. Summary cards (active, on-trial, est. MRR, period spend) + a subscriber table (email, tier, status, renew/trial, joined, requests, est. cost, claimed/unclaimed). `lib/admin.ts`: `isAdmin(email)` checks the verified session email against `ADMIN_EMAILS`; `getAllAccounts()` reads all licenses + usage behind that gate. The "Admin" link shows only to allow-listed emails. No per-customer admin actions yet.
- **Desktop → web sync, web side** (PR #56, merged): `account_sync` table (`migration_sync.sql` — `profile`/`stats`/`connections` JSONB + `app_version`, one row per license, on-delete cascade, RLS-on no public policy). Authed ingest `POST /api/proxy/sync` (X-License-Key via `authorizeLicense`) with a strict allow-list sanitizer (known fields only, types coerced, extras dropped, no message content). Dashboard renders Profile, "Speech & activity" (tasks, success rate, avg time, top requests) and Connected apps; degrades to a "waiting for your app" state before first sync. `lib/account.ts getLatestAccountSync`.
- **Desktop → web sync, app side** (PR #57, OPEN — not merged): `server.py` wires the previously-test-only `SuccessTracker` (`tracking.py`) into the live loop at two choke points — `_track_usage(action)` at chat dispatch (top requests) and `_track_task(type, success, duration)` + connection detection in the shared `_lookup_and_report` wrapper (success rate, avg duration, calendar/mail/notes "seen working" flags). No-op-safe; the voice hot path is untouched on tracker failure. Tracker DB opens in the writable data dir via a new `valet_data_dir()` helper. A telemetry-gated background loop (`_account_sync_loop`, 20s after start then every 15 min, gated on LICENSE_KEY present AND VALET_TELEMETRY not opted out) pushes `_gather_sync_snapshot()` (onboarding profile env + tracker aggregates + connection flags + app version; skips the "sir" USER_NAME placeholder; no message content) to `/api/proxy/sync`. Verified: server.py compiles, 26 SuccessTracker tests pass, snapshot shape matches the sanitizer.
- **Migrations run in prod:** `supabase/migration_accounts.sql` (adds `customer_email`, `user_id`, indexes) and `supabase/migration_sync.sql` have both been applied to production Supabase.

### Proxy analytics + checkout
- **Privacy-respecting action analytics** (`product-site/lib/proxy/anthropic.ts`, `langfuse.ts`; PRs #39, #40). A streaming transform extracts `[ACTION:TYPE] target` tags from the assistant's reply and logs them to Langfuse as trace tags + metadata (bare type, e.g. `open_app`, plus `app:Spotify`-style target tags). No raw prompts or responses are stored (`PROXY_CAPTURE_PAYLOADS` off by default); sensitive targets (file paths on deletes/builds) are dropped, only app/project names kept.
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
| Accounts + Licensing DB | Supabase Postgres (Pro) + Supabase Auth | `licenses` (+ `customer_email`, `user_id`), `license_usage`, `account_sync` tables; email/password auth via `@supabase/ssr` |
| Observability | Langfuse | license as user id, payloads scrubbed |
| Billing | Stripe (LIVE) | Free / Pro $20/mo / Ultra $50/mo — live mode; payouts paused pending bank account |
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
| Supabase | accounts (Auth) + license/usage/sync store (`licenses`, `license_usage`, `account_sync`) | Pro plan | live |
| Langfuse | proxy tracing + privacy-respecting action analytics (tags/metadata, no raw payloads) | unknown | live |
| Langfuse MCP | analytics access for a planned usage dashboard | unknown | connected (dashboard planned) |
| Stripe | checkout + subscriptions + Billing Portal (Free / Pro $20/mo / Ultra $50/mo) | per-transaction | live (Twin Peaks Labs; payouts paused) |
| Resend | transactional email (license key on purchase) | unknown | planned (no-op until `RESEND_API_KEY`/`EMAIL_FROM` set) |
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

- **2026-06-11 — Stripe flipped to LIVE under a new "Twin Peaks Labs" account** — Moved off sandbox to live keys, a live webhook on the www host, recreated Pro/Ultra products with new live price IDs, and a saved Customer Portal config. Verified `cs_live_` checkout for both tiers end to end. Payouts deliberately left paused until a bank account is added (activation incomplete); live charges accrue in the balance meanwhile.
- **2026-06-11 — Identity = Supabase Auth accounts, license auto-claimed by buyer email** — Added real email/password login rather than keeping license-key-in-a-header as the only identity. New accounts auto-link their license by matching the Stripe buyer email; a manual link-key form covers mismatches. Email verification is enforced for the web session, but the license keeps working in the app regardless so confirmation friction never blocks usage.
- **2026-06-11 — Desktop→web sync is app-pushes-snapshot, sanitized, telemetry-gated, never message content** — The app pushes a profile/stats/connections snapshot to an authed ingest endpoint every 15 min; the server applies a strict allow-list sanitizer (known fields only, types coerced, extras dropped). Gated on a license key present AND telemetry not opted out. No message content ever leaves the machine. Web-side profile is read-only (synced down); editing-on-web that writes back to the app is deferred.
- **2026-06-11 — Owner admin view is read-only, env allow-list gated** — `/account/admin` is gated on `ADMIN_EMAILS` checked against the verified session email; it only reads (summary cards + subscriber table) with no per-customer actions yet. Keeps the blast radius minimal while giving the owner visibility.
- **2026-06-11 — `NEXT_PUBLIC_SUPABASE_URL` must be the bare project URL** — A `/rest/v1/` suffix produced a PostgREST "Invalid path specified in request URL" error at signup. Fixed to the bare `https://ufqvgujnphaejewqmugg.supabase.co`. Noted so it isn't reintroduced.
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
- [ ] **Add a bank account in Stripe to resume payouts** — live charges accrue but cannot pay out until account activation is complete — owner: Finley
- [ ] **Wire transactional email** — license-key email (Resend, `lib/email.ts`) is a no-op until `RESEND_API_KEY`/`EMAIL_FROM` are set; Supabase's built-in confirmation email is rate-limited/unreliable, so wire custom SMTP (e.g. Resend) in Supabase Auth before real distribution. Until then, self-confirm users in the Supabase dashboard — owner: Finley
- [ ] **Add the www `/auth/callback` redirect URL in Supabase** — Site URL is `https://www.valet-voice.com/`, so confirmation links from www need it (apex variant already added) — owner: Finley
- [ ] **Merge the desktop sync (PR #57)** — web side is live; the `server.py` tracker wiring + push loop is still open — owner: Finley
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
- **Stripe payouts are PAUSED.** Live mode is on and verified (`cs_live_` sessions for both tiers, a real trial purchase), but account activation is incomplete — live charges accrue in the Stripe balance and cannot pay out until a bank account is added.
- **Transactional email is not fully wired.** The license-key email (Resend, `lib/email.ts`) is a silent no-op until `RESEND_API_KEY`/`EMAIL_FROM` are set. Separately, Supabase's built-in confirmation email is rate-limited and unreliable; custom SMTP (e.g. Resend) should be wired in Supabase Auth before real distribution. Until then, users can be self-confirmed in the Supabase dashboard. The www `/auth/callback` redirect URL should also be added in Supabase (apex is done) so www confirmation links resolve.
- **License recovery now works via accounts, but depends on the email above.** Customers can sign in at `/account` and see their key any time (auto-claimed by buyer email, or via the manual link-key form). This closes the old "key only shown once on the success page" gap — but only once confirmation email is reliable, since signup requires a verified address.
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
- *(2026-06-11) DONE: Stripe is now LIVE.* Remaining: add a bank account to resume payouts.
- Resend (`RESEND_API_KEY` + `EMAIL_FROM`) for the license email; and custom SMTP in Supabase Auth so confirmation email is reliable. Add the www `/auth/callback` redirect URL in Supabase.

**Deferred features (not started):**
- **Google Calendar / Gmail:** per-user OAuth + Google app verification. Onboarding shows "Coming soon" placeholders. Real project.
- ~~**User accounts / self-serve billing portal**~~ — *(2026-06-11) BUILT this session: Supabase-Auth accounts, `/account` dashboard (key, plan, usage, download, Stripe Billing Portal), owner `/account/admin`, and a desktop→web sync. Web side merged; desktop sync (PR #57) open.* Remaining: edit-profile-on-web that writes back to the app (possible Phase 3).
- **"Sir" persona pass:** roughly 20 instances in the app system prompt instruct Vee to address the user as "sir" (the old assistant persona). A deliberate tone decision plus an app rebuild.
- **TTS fallback:** Fish Audio is the single vendor with no backup. Add a secondary provider path.
- **Universal app control via the macOS Accessibility API (AXUIElement):** drive any app's UI by reading its accessibility tree and sending synthetic input. Big bet, do it selectively (native apps first where the tree is rich, screenshot + vision fallback for Electron / weak trees, every click / type gated behind the existing confirmation + kill switch).
- **Langfuse usage dashboard:** the Langfuse MCP is connected; build the apps-opened / actions-done / friction widgets (needs live action-tag data flowing first).

**Top liability status (context, already handled):** shared-key spend is now BOUNDED via `FAIR_USE_MODE=throttle` + `FAIR_USE_MONTHLY_USD=10` in Vercel (per-license cap live).

---

## Links  [rewrite]

- **Live URL (proxy + marketing + accounts):** https://www.valet-voice.com (Vercel; `jarvis-y.vercel.app` is the underlying deploy)
- **Customer account portal:** https://valet-voice.com/account (owner admin at `/account/admin`)
- **App:** local-only (`http://localhost:5173`)
- **GitHub:** https://github.com/Kuba-Ventures/jarvis-y (remote/URL names deliberately NOT renamed; org is Kuba-Ventures; local dir is `~/Code/VALET`)
- **Staging:** n/a
- **Client Drive folder:** n/a
- **Slack channel:** n/a
- **Download (release DMG):** https://github.com/Kuba-Ventures/valet-downloads/releases/tag/v0.1.0 (`VALET_0.1.0_aarch64.dmg`, signed + notarized; Vercel `DOWNLOAD_URL` points here)
- **Related repos:** `Kuba-Ventures/valet-downloads` (public, hosts the signed DMG releases); `product-site/` (the Next.js marketing + proxy site, in this repo); Kuba-Ventures soultech / Dharma (the PR-factory pattern this repo mirrors)

---

## Changelog  [append-only — never rewrite or delete]

- **2026-06-11:** kuba-vault refresh — Stripe went LIVE (new "Twin Peaks Labs" account, live keys/webhook/prices, `cs_live_` verified for Pro + Ultra, a real trial purchase issued a key; payouts paused pending bank account), and a full self-service account layer shipped on valet-voice.com: Supabase-Auth email/password login + `/account` section (PR #52), show-password toggle + confirm field then eye-icon-in-field (PRs #53, #55), owner-only read-only `/account/admin` subscriber view (PR #54), and the web side of a desktop→web sync — `account_sync` table, authed sanitized `/api/proxy/sync` ingest, dashboard Profile/Speech-activity/Connected-apps sections (PR #56). Accounts + sync migrations run in prod Supabase. Desktop sync app side (PR #57: `server.py` wires `SuccessTracker` + a telemetry-gated 15-min snapshot push) is OPEN, not merged. Updated tech stack/integrations (Supabase Auth + `account_sync`, Stripe live, Resend planned), logged five decisions, refreshed open loops (bank account, transactional email, www redirect, merge #57) and risks (payouts paused, email not wired, recovery now via accounts). Closed the old "no license-key recovery" gap.
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
