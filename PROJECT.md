# VALET
*Voice-Activated Local Engineering Terminal — a downloadable, license-gated macOS voice assistant.*

*Last updated: 2026-06-11 23:55 ET by kuba-vault*

---

## TL;DR  [rewrite]

VALET (formal name; answers to "Vee") is a local, voice-first macOS assistant with a British-butler persona, an audio-reactive Three.js orb, and a live process panel. The full commercial loop is real and LIVE: buy, get a license-key email, sign in at valet-voice.com, download a signed/notarized macOS app, install with no Gatekeeper warning, run. **The user is stepping away — read the "What's left" section first; it is the pickup list.** Since the last update (which covered the account portal, live Stripe, and the desktop→web sync): the per-plan fair-use *mechanism* shipped (#59) so the top shared-key risk is finally addressable — it just needs flipping on (`FAIR_USE_MODE=throttle` + per-plan envs in Vercel); customers now see usage as a percentage with no dollars exposed (#60); the site nav is auth-aware (#61); and the app got real polish — no duplicate backend Dock icon (#62), three-dot opens Settings (#63), and Settings split into User + Console with native-mic Permissions and a Connectors section (#64). Transactional email is now fully LIVE (Resend domain verified, license-key email sends, Supabase custom SMTP delivers confirmation/reset), and account creation is verified end-to-end on a clean slate. Two PRs are OPEN and are the first thing to do on return: **#65** (open-Notes launches the app, backend, needs rebuild) and **#66** (device-settings Phase 1 storage/endpoints, needs `migration_device_settings.sql`). Stripe **payouts are still PAUSED** until the client adds a bank account.

---

## What it is  [rewrite when value prop evolves]

**The problem:** Talking to LLM chat boxes is slow, and stitching together Calendar, Mail, a browser, terminals and a coding agent to get one thing done is slower. Shipping that as a product also means no end user should have to paste in their own API keys.
**The solution:** A single voice loop on the Mac that routes intent — conversation, system lookup, research, file/app control, ship-to-Cursor — without leaving the orb, with all AI billed centrally behind a license so the download ships with zero vendor secrets.
**The user:** A macOS builder who lives in Claude Code, Cursor and the Apple suite and wants hands-free orchestration — and, post-Phase-2, a paying customer who buys a license and downloads a signed app.
**The value:** Sub-second voice for the simple stuff, full Opus + web tools for the hard stuff, native macOS control with a safety net — and a clean install with no key management.

---

## Status  [rewrite]

- **Phase:** launch prep — full buy/email/login/download/install/run loop is real and live; transactional email works; remaining work is enabling the shared-key fair-use limit, finishing payout setup, and landing two open PRs.
- **Engagement manager:** self-directed
- **Lead:** Finley (**stepping away — see "What's left to pick up"**)
- **Cadence:** continuous (per-PR through the supervised factory)
- **Next milestone:** on return — merge PRs #65 + #66 (run `migration_device_settings.sql`), then flip `FAIR_USE_MODE=throttle` + per-plan allowance envs in Vercel to close the top risk. No hard date.
- **Flags:** shipping

---

## Where we are right now  [rewrite]

Finley is **stepping away**; this session hardened the now-live commercial loop and made the *mechanism* for the top risk real (without turning it on). Highlights since the last update, all merged to `main`: **per-plan fair-use allowances** (#59) — the ceiling is now per plan (`FAIR_USE_USD_PRO` / `FAIR_USE_USD_ULTRA`, fallback `FAIR_USE_MONTHLY_USD`) threaded through `lib/proxy/{auth,usage,anthropic,fish}.ts`; this is the lever that turns the soft cap into a real per-license limit, but it **does not enforce yet** (`FAIR_USE_MODE` still defaults to `warn`). Customers now see usage as a **percentage** with no dollar spend or limit exposed (#60, server-component so raw numbers never reach the browser); owner admin still shows $/MRR. Site nav is **auth-aware** (#61) — signed-in users get an "Account" button, signed-out get "Log in" + "Start free trial", detected client-side so marketing pages stay static. App polish: the PyInstaller backend no longer shows a **second Dock icon** (#62, set faceless via `NSApplicationActivationPolicyProhibited`); the **three-dot button opens Settings directly** (#63, dropped the dev-only Restart/Fix items); and **Settings split into User + Console** (#64) — Console (renamed from "Computer Settings") gained a Permissions section (Microphone via native getUserMedia, Automation, Full Disk Access — each enable + Re-check, reusing `/api/permissions/{status,open}`) and a Connectors section (Google moved here), User keeps personal info.

Operationally, **transactional email is now fully LIVE**: Resend domain `valet-voice.com` verified (DKIM/SPF/MX), Vercel `RESEND_API_KEY` + `EMAIL_FROM` set so the license-key email sends on purchase, and Supabase custom SMTP wired to Resend (`smtp.resend.com:465`) so signup-confirmation and password-reset emails deliver reliably (Supabase's rate-limited default was silently failing and blocking account creation). Added the www `/auth/callback` redirect in Supabase and fixed a `NEXT_PUBLIC_SUPABASE_URL` `/rest/v1/` suffix that broke signup. **Account creation is now verified end-to-end on a clean slate** (signup → confirmation email from valet-voice.com → dashboard → license auto-claimed). Test-data cleanup done: the pile of auto-claimed test trials on finley@qsbsrollover.com was removed (Stripe customers deleted so trials don't convert, `licenses` rows + the auth user deleted; use `+alias` emails for future testing). Two unsigned local builds were cut: the **duplicate Dock icon turned out to be stale/mounted DMG volumes** (8+) + a trashed copy polluting Launch Services, NOT a code bug — ejected + rebuilt the LS database, single icon confirmed; also removed stale `com.jarvis.*` LaunchAgents.

Two PRs are **OPEN** and are the first thing to pick up: **#65** (`_looks_like_app()` strips trailing "on my computer/mac" / " app" so "open Notes on my computer" launches the app — backend, needs a rebuild) and **#66** (device-settings Phase 1 — `device_settings` table + `lib/device-settings.ts` + read/write endpoints; deploy needs `supabase/migration_device_settings.sql`). See the next section for the full pickup list.

---

## What's left to pick up  [rewrite — read this first]

Finley is stepping away. This is the exact next-step list, ordered.

1. **Merge the two open PRs.** #65 (open-Notes launches the app) and #66 (device-settings Phase 1). For #66, run `product-site/supabase/migration_device_settings.sql` against prod Supabase before/with deploy. #65 is a backend change — needs an app rebuild to take effect.
2. **Turn ON fair-use enforcement** (this closes the top risk; the mechanism already shipped in #59). In Vercel set `FAIR_USE_MODE=throttle` (or `block`) and the per-plan envs `FAIR_USE_USD_PRO` / `FAIR_USE_USD_ULTRA`. Until this is set it stays `warn` — i.e. the shared-key spend risk is open.
3. **Finish device-settings Phases 2–4** (bidirectional "manage VALET from the browser"; #66 is Phase 1 = storage + endpoints only):
   - **Phase 2:** editable "Device settings" card on `/account` (voice Male/Female, **Voice ID moves here from the app**, telemetry toggle).
   - **Phase 3:** `server.py` fetches `/api/proxy/device-settings` on startup + poll and applies via `/api/settings/keys`; then **remove the Proxy URL + Voice ID fields from the app's Console Settings**.
   - **Phase 4:** conflict resolution (web wins) + decide poll cadence.
4. **Fix mic-green permission detection (backend).** `/api/permissions/status` returns null for Microphone, so the indicator never turns green even when granted — affects both onboarding and the new Console Permissions section. Needs a real mic/speech TCC authorization check via the bundled pyobjc.
5. **Design decision:** should "open my calendar" / "open email" launch the app or show the data? Today they route to the calendar/mail READ lookups (show schedule / inbox), not app launch. Left as an open design call (this was the root cause of "open notes/calendar/email did nothing" — intent routing, not the build; the "on my computer" variant is fixed in #65).
6. **Cut a fresh signed + notarized release** once #65/#66 land. The currently-installed app is an unsigned test build; signed builds keep their TCC grants and pass Gatekeeper.
7. **Stripe payouts** — client's responsibility: add a bank account to resume payouts. Charges work; funds just can't pay out yet.

---

## What's built  [rewrite]

### Stage A — Proxy spine (LIVE in prod)
- License-gated AI/TTS proxy in `product-site/` (Next.js on Vercel, https://jarvis-y.vercel.app). Routes: `app/api/proxy/{completion,research,tts,usage}` plus a native Anthropic-dialect `app/api/proxy/v1/messages`.
- Backed by `product-site/lib/proxy/{auth,pricing,usage,langfuse,anthropic,fish}.ts`. Anthropic + Fish keys live server-side; the downloadable app ships with no vendor secrets.
- Every call validated against the Supabase `licenses` table; usage metered per license (requests, tokens, est. cost) against a **per-plan** fair-use allowance (`FAIR_USE_USD_PRO` / `FAIR_USE_USD_ULTRA`, fallback `FAIR_USE_MONTHLY_USD`; #59). Behavior is still **soft-warn** because `FAIR_USE_MODE` defaults to `warn` — it never blocks until the owner sets `FAIR_USE_MODE=throttle`/`block`.
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
- **Desktop → web sync, app side** (PR #57, MERGED): `server.py` wires the previously-test-only `SuccessTracker` (`tracking.py`) into the live loop at two choke points — `_track_usage(action)` at chat dispatch (top requests) and `_track_task(type, success, duration)` + connection detection in the shared `_lookup_and_report` wrapper (success rate, avg duration, calendar/mail/notes "seen working" flags). No-op-safe; the voice hot path is untouched on tracker failure. Tracker DB opens in the writable data dir via a new `valet_data_dir()` helper. A telemetry-gated background loop (`_account_sync_loop`, 20s after start then every 15 min, gated on LICENSE_KEY present AND VALET_TELEMETRY not opted out) pushes `_gather_sync_snapshot()` (onboarding profile env + tracker aggregates + connection flags + app version; skips the "sir" USER_NAME placeholder; no message content) to `/api/proxy/sync`.
- **Migrations run in prod:** `supabase/migration_accounts.sql` (adds `customer_email`, `user_id`, indexes) and `supabase/migration_sync.sql` have both been applied to production Supabase. `supabase/migration_device_settings.sql` (for #66) is **not yet applied** — run it when #66 lands.

### Hardening + per-plan limits (LATEST — since the account portal / live-Stripe update)
- **Per-plan fair-use allowances** (PR #59): the fair-use ceiling is now per plan — `FAIR_USE_USD_PRO` / `FAIR_USE_USD_ULTRA` with `FAIR_USE_MONTHLY_USD` as fallback — threaded through enforcement (`lib/proxy/{auth,usage,anthropic,fish}.ts`) and the displayed cap. This is the mechanism that turns the soft cap into a real per-license limit. It does **not** enforce yet: `FAIR_USE_MODE` still defaults to `warn`. To close the shared-key risk the owner must set `FAIR_USE_MODE=throttle` (or `block`) + the per-plan allowance envs in Vercel.
- **Usage shown as a percentage** (PR #60): the customer dashboard shows usage as a % only — no dollar spend or limit exposed. A server component computes it so the raw numbers never reach the browser. Owner admin still shows $ / MRR.
- **Auth-aware nav** (PR #61): signed-in users see a blue "Account" button; signed-out see "Log in" + "Start free trial". Client-side session detection so marketing pages stay static.
- **No duplicate backend Dock icon** (PR #62): the PyInstaller backend imports AppKit (pyobjc) which gave it a Dock icon; now set faceless via `NSApplicationActivationPolicyProhibited`. Good hygiene — but NOT the actual cause of the duplicate-Dock issue the user saw (that was stale mounted DMG volumes + a trashed copy polluting Launch Services).
- **Three-dot opens Settings** (PR #63): the app's three-dot button opens Settings directly; removed the dev-only "Restart Server" / "Fix Yourself" dropdown items.
- **Settings split into User + Console** (PR #64): "Computer Settings" → "Console Settings". Console gained a **Permissions** section (Microphone via native getUserMedia prompt, Automation, Full Disk Access — each enable + Re-check, reusing `/api/permissions/{status,open}`) and a **Connectors** section (Google moved here). User tab keeps personal info (name, honorific, calendar accounts, DOB, location, bio).
- **Open PRs not yet merged:** **#65** — `_looks_like_app()` strips trailing locational suffixes ("on my computer/mac/...", " app") so "open Notes on my computer" launches the app rather than the project resolver (backend; needs a rebuild). **#66** — device-settings Phase 1: new `device_settings` table + `lib/device-settings.ts` + `GET /api/proxy/device-settings` (app reads) + `GET/POST /api/account/device-settings` (web reads/writes); storage + endpoints only; deploy needs `supabase/migration_device_settings.sql`.
- **Transactional email LIVE:** Resend domain `valet-voice.com` verified (DKIM/SPF/MX); Vercel `RESEND_API_KEY` + `EMAIL_FROM` set (license-key email sends on purchase); Supabase custom SMTP wired to Resend (`smtp.resend.com:465`, user `resend`) so signup-confirmation + password-reset emails deliver. www `/auth/callback` redirect added in Supabase; `NEXT_PUBLIC_SUPABASE_URL` `/rest/v1/` suffix removed. Account creation verified end-to-end on a clean slate.

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
| Accounts + Licensing DB | Supabase Postgres (Pro) + Supabase Auth | `licenses` (+ `customer_email`, `user_id`), `license_usage`, `account_sync`, `device_settings` (#66, migration not yet applied) tables; email/password auth via `@supabase/ssr`; custom SMTP via Resend |
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
| Resend | transactional email — license key on purchase + Supabase custom SMTP for confirmation/reset | unknown | live (domain `valet-voice.com` verified; `RESEND_API_KEY`/`EMAIL_FROM` set; SMTP `smtp.resend.com:465`) |
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

- **2026-06-11 — Fair-use cap is now per-plan, but enforcement stays OFF until explicitly enabled** — Shipped the per-plan allowance mechanism (`FAIR_USE_USD_PRO`/`FAIR_USE_USD_ULTRA`, #59) so the shared-key risk is finally closable, but left `FAIR_USE_MODE=warn` (never blocks) so a misconfigured limit can't break paying customers mid-launch. Flipping to `throttle`/`block` is a deliberate, owner-owned step.
- **2026-06-11 — Customers see usage as a percentage, never dollars or the limit** — The customer dashboard exposes only a % (computed server-side so raw cost/limit never reach the browser, #60). Owner admin keeps $/MRR. Avoids anchoring customers on our cost basis or the exact ceiling.
- **2026-06-11 — Duplicate Dock icon was an environment artifact, not a code bug** — Diagnosed the user's two-Dock-icons report as 8+ stale/mounted VALET DMG volumes plus a trashed copy polluting Launch Services, fixed by ejecting + rebuilding the LS database. The backend-faceless change (#62) is good hygiene but was kept separately on its own merits, not as "the fix".
- **2026-06-11 — "open notes/calendar/email did nothing" is intent routing, not the build** — "open X on my computer" hit the project resolver (fixed in #65 by stripping locational suffixes). "open my calendar"/"open email" route to the READ lookups (show schedule/inbox) rather than launching the app — left as an OPEN design decision rather than silently changing it.
- **2026-06-11 — Use `+alias` emails for test purchases** — A pile of test trials had all auto-claimed onto the owner's real email; cleaned up (Stripe customers + `licenses` rows + auth user deleted so trials don't convert to charges). Future testing uses `+alias` addresses so test data is isolated.
- **2026-06-11 — Device-settings ("manage VALET from the browser") ships in 4 phases, web wins on conflict** — Phase 1 (#66) is storage + endpoints only; Phases 2–4 add the editable `/account` card, `server.py` fetch/poll/apply, removal of Proxy URL + Voice ID from the app, and conflict resolution. Web is the source of truth on conflict. Sequenced so each phase is independently shippable.
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

- [ ] **Merge open PRs #65 + #66** — #65 (open-Notes launches the app, backend, needs rebuild); #66 (device-settings Phase 1, run `migration_device_settings.sql`) — owner: Finley (see "What's left to pick up")
- [ ] **Turn ON fair-use enforcement** — mechanism shipped (#59); set `FAIR_USE_MODE=throttle` + `FAIR_USE_USD_PRO`/`FAIR_USE_USD_ULTRA` in Vercel to close the top shared-key risk — owner: Finley
- [ ] **Device-settings Phases 2–4** — editable `/account` card (Voice ID moves here), `server.py` fetch/poll/apply + remove Proxy URL/Voice ID from app, conflict resolution (web wins) — owner: Finley
- [ ] **Fix mic-green permission detection (backend)** — `/api/permissions/status` returns null for Microphone; needs a real mic/speech TCC check via bundled pyobjc (affects onboarding + Console Permissions) — owner: Finley
- [ ] **Design decision: "open my calendar"/"open email" — launch app or show data?** — currently route to READ lookups; open call — owner: Finley
- [ ] **Cut a fresh signed + notarized release** once #65/#66 land — currently-installed app is an unsigned test build — owner: Finley
- [ ] **Add a bank account in Stripe to resume payouts** — live charges accrue but cannot pay out until account activation is complete — owner: client
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

- **TOP RISK (now ADDRESSABLE, not yet closed) — shipped users bill the owner's personal keys with the cap in warn mode.** Every shipped user's AI + TTS runs through the proxy's single owner-held Anthropic + Fish keys. The per-plan fair-use mechanism now exists (#59) but `FAIR_USE_MODE` still defaults to `warn`, so it never blocks. With a signed build live, anyone with a license can drive unbounded spend until the owner sets `FAIR_USE_MODE=throttle`/`block` + the per-plan allowance envs in Vercel. **This is the single concrete step to close the risk** (see "What's left", item 2).
- **Mic-green permission detection is broken (backend).** `/api/permissions/status` returns null for Microphone, so the green indicator never lights even when mic is granted — in both onboarding and the new Console Permissions section. Needs a real mic/speech TCC authorization check via the bundled pyobjc.
- **Currently-installed app is an unsigned test build.** The signed/notarized DMG (`v0.1.0`) is live for download, but the build on the dev machine is unsigned and won't retain TCC grants across rebuilds. Cut a fresh signed + notarized release once #65/#66 land.
- **Stripe payouts are PAUSED.** Live mode is on and verified, but account activation is incomplete — live charges accrue in the Stripe balance and cannot pay out until the client adds a bank account.
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
- *(2026-06-11) DONE: Stripe is now LIVE.* Remaining: add a bank account to resume payouts (client's responsibility).
- *(2026-06-11) DONE: transactional email is LIVE* — Resend domain verified, `RESEND_API_KEY`/`EMAIL_FROM` set, Supabase custom SMTP wired, www `/auth/callback` redirect added.
- **OPEN: turn ON fair-use enforcement** — set `FAIR_USE_MODE=throttle` + `FAIR_USE_USD_PRO`/`FAIR_USE_USD_ULTRA` in Vercel. Mechanism shipped (#59); flipping it on is the step that actually closes the shared-key risk.

**Deferred features (not started):**
- **Google Calendar / Gmail:** per-user OAuth + Google app verification. Onboarding shows "Coming soon" placeholders. Real project.
- ~~**User accounts / self-serve billing portal**~~ — *BUILT: Supabase-Auth accounts, `/account` dashboard (key, plan, %-usage, download, Stripe Billing Portal), owner `/account/admin`, desktop→web sync (PR #57 now merged).* Remaining: device-settings Phases 2–4 — editable settings on `/account` that write back to the app (Phase 1 = #66, open).
- ~~**Manage VALET from the browser (device-settings)**~~ — *Phase 1 storage + endpoints = PR #66 (open). Phases 2–4 build the editable card, app fetch/poll/apply, and conflict resolution. See "What's left".*
- **"Sir" persona pass:** roughly 20 instances in the app system prompt instruct Vee to address the user as "sir" (the old assistant persona). A deliberate tone decision plus an app rebuild.
- **TTS fallback:** Fish Audio is the single vendor with no backup. Add a secondary provider path.
- **Universal app control via the macOS Accessibility API (AXUIElement):** drive any app's UI by reading its accessibility tree and sending synthetic input. Big bet, do it selectively (native apps first where the tree is rich, screenshot + vision fallback for Electron / weak trees, every click / type gated behind the existing confirmation + kill switch).
- **Langfuse usage dashboard:** the Langfuse MCP is connected; build the apps-opened / actions-done / friction widgets (needs live action-tag data flowing first).

**Top liability status:** the per-plan cap *mechanism* shipped (#59), so shared-key spend is now BOUNDABLE — but enforcement is **not yet on** (`FAIR_USE_MODE` defaults to `warn`). It becomes bounded only once the owner sets `FAIR_USE_MODE=throttle` + `FAIR_USE_USD_PRO`/`FAIR_USE_USD_ULTRA` in Vercel. *(Corrects the earlier note that claimed this was already enabled — it was not.)*

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

- **2026-06-11 (late eve):** kuba-vault refresh + handoff — Finley stepping away, added a prominent "What's left to pick up" section. Since the account-portal/live-Stripe update, merged to main: per-plan fair-use allowances (#59, mechanism only — `FAIR_USE_MODE` still `warn`), customer usage shown as a % with no dollars exposed (#60), auth-aware site nav (#61), backend no-duplicate-Dock-icon (#62), three-dot opens Settings (#63), Settings split into User + Console with native-mic Permissions + Connectors (#64). PR #57 (desktop sync app side) confirmed merged. Operationally: transactional email now LIVE (Resend domain verified, license-key email sends, Supabase custom SMTP for confirm/reset), www `/auth/callback` redirect added, `NEXT_PUBLIC_SUPABASE_URL` suffix fixed, account creation verified end-to-end; test-trial data cleaned up; duplicate-Dock-icon diagnosed as stale DMG volumes (not a code bug), stale `com.jarvis.*` LaunchAgents removed. OPEN: #65 (open-Notes launches app, backend) and #66 (device-settings Phase 1, needs `migration_device_settings.sql`). Logged six decisions, rewrote open loops + risks (top shared-key risk now ADDRESSABLE via #59 but not yet enabled; new mic-detection + unsigned-build risks; email risk cleared), corrected the earlier "fair-use already bounded" note.
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
