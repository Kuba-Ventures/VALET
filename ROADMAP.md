# VALET Roadmap: fast, intuitive voice control of the Mac, shipped as a signed, license-gated download

*Owner: Finley · Started: 2026-10-02 · Status: shipping v0.2.44, no product code merged since 2026-07-20 · last verified against the code 2026-10-02*

## What this is

VALET is a voice-first macOS menu-bar assistant (British-butler persona "Vee") that hears a held ⌃⌥ push-to-talk chord, answers out loud, and drives the Mac: apps, files, settings, Calendar, Mail (read-only), Notes, the browser, and Claude Code. It is sold as a license-gated, signed and notarized DMG for Mac builders who want hands-free orchestration. Stack: Python/FastAPI backend (`server.py` and siblings) bundled with PyInstaller, a Vite + TypeScript + Three.js frontend (`frontend/`), a Tauri 2 shell (`src-tauri/`), and a Next.js + Supabase + Stripe license/AI proxy on Vercel (`product-site/`). Public builds are published on `Kuba-Ventures/valet-downloads`.

### Status legend

- [ ] not started · [~] in progress · [x] done
- Tags: **(design)** **(build)** **(growth)** **(compliance)**
- ⚠️ = critical path

---

## Stage 0: Commercial loop and safety foundation (done)

- [x] **(build)** License-gated AI/TTS proxy so the app ships with no vendor keys. `product-site/app/api/proxy/{completion,research,tts,usage,v1}`, `product-site/lib/proxy/`.
- [x] **(build)** App routes all AI and TTS through the proxy; `licensing.py` validates with a 7-day offline grace window.
- [x] **(build)** Portable control layer and risk-tiered safety (Tier 0 auto, Tier 1 confirm, kill switch). `action_executor.py`, `applescript_executor.py`, `safety.py`, `safe_executor.py`.
- [x] **(build)** Packaging, Developer ID signing and notarization. `packaging/build-macos.sh`, `packaging/valet.spec`.
- [x] **(build)** Stripe live, Supabase email/password accounts, account-to-license linking, customer dashboard and owner admin view (Kuba-Ventures/VALET#52 to #56, #54).
- [x] **(build)** Desktop-to-web sync and device settings Phases 1 to 3 (Kuba-Ventures/VALET#56, #57, #66, #68, #69).
- [x] **(compliance)** Duplicate-license fix is on `main`: `upsertLicenseFromSubscription` now upserts with `onConflict: "stripe_subscription_id"` (`product-site/lib/license.ts`), with `product-site/supabase/migration_dedupe_licenses.sql` and `migration_licenses_onconflict_fix.sql`. Whether the migration has been run in production is not recorded (see Open questions).
- [x] **(build)** Branch protection on `main` requires branches to be up to date (`strict: true`) plus the `factory-tests` and `factory-review` checks (`.github/workflows/factory.yml`).

## Stage 1: Native Mac control and the menu-bar product (done, shipped since v0.2.0)

- [x] **(build)** No-LLM voice console, visible cursor glide with hit-test-guarded click, point-and-teach, latency harness, PTT-primary (Kuba-Ventures/VALET#113 to #123). Plan: `coding-plans/voice-mac-control-build-plan.md`.
- [x] **(build)** Menu-bar shell with global ⌃⌥ chord in the main Tauri process (`spawn_global_chord`, `src-tauri/src/main.rs`) and multi-monitor cursor follower (Kuba-Ventures/VALET#135, #136).
- [x] **(build)** Voice-native Raycast: file, settings and system-action search with no LLM round-trip. `file_index.py`, `settings_index.py`, `system_actions.py` (Kuba-Ventures/VALET#137, #138).
- [x] **(build)** Guided walkthroughs, teach-don't-do, working end to end on-device. `walkthrough.py`, `tests/test_walkthrough.py` (Kuba-Ventures/VALET#139, #167 to #174).
- [x] **(design)** Voice-led violet onboarding and menu-bar UI polish (Kuba-Ventures/VALET#153, #155 to #160, #165).
- [x] **(build)** Compose-and-stop messaging (text, email, Slack) and Apple Clock control: VALET never auto-sends to a person (Kuba-Ventures/VALET#194 to #197).

## Stage 2: Gmail voice MVP and the 0.2.27 to 0.2.44 release train (done)

- [x] **(build)** Batch-read a day's Gmail inbox into one spoken digest, from inbox-row previews (Kuba-Ventures/VALET#295 to #303, #307, #308, #312). Opening each email is a recorded non-goal, not a TODO (`PROJECT.md`, Decisions).
- [x] **(build)** Save the last summary to a new Apple Note, structured as per-email cards with action items as Reminders (Kuba-Ventures/VALET#306, #313 to #315, #328).
- [x] **(build)** Scroll actions, "scroll until you find X", scroll a named window (Kuba-Ventures/VALET#317, #319; `tests/test_scroll.py`).
- [x] **(build)** Deepgram STT for the push-to-talk turn only, with fallback to the built-in recognizer (Kuba-Ventures/VALET#320, #325; `deepgram_stt.py`).
- [x] **(build)** Misheard product names corrected before routing (Kuba-Ventures/VALET#323).
- [x] **(build)** Signed-out Gmail recovers through guided login; VALET never types the password (Kuba-Ventures/VALET#326, #327, #329).
- [x] **(build)** v0.2.44 released: `src-tauri/tauri.conf.json` is `0.2.44`, and `Kuba-Ventures/valet-downloads` tag `v0.2.44` is marked Latest (2026-07-20). Note: `PROJECT.md` still says the last public download is 0.2.26, which is stale.

## Stage 3: Ship-blockers and cleanup on the current product (next)

- [ ] ⚠️ **(compliance)** Gate Deepgram STT behind the license proxy with a per-user spend cap. Today the key lives only in one machine's `~/Library/Application Support/VALET/.env`; there is no Deepgram route in `product-site/` (Kuba-Ventures/VALET#321).
- [ ] **(build)** Synthetic-input target-focus hardening and the owed live in-app passes (UC4 confirm/STOP, UC5 barge-in echo tuning) before UC4/UC5/UC6-terminal ship in a signed build (`PROJECT.md`, What's left items 6 and 7; `tests/test_uc4_loop.py`).
- [~] **(build)** Code-quality audit and removal plan, docs only so far (Kuba-Ventures/VALET#334, open). It flags `qa.py`/`suggestions.py` as broken in production by a swallowed import error, which is a fix-or-delete decision.
- [ ] **(design)** Cursor follower dot is still blue (`#2a86ff` in `src-tauri/loading/overlay.html`); bring it in line with the violet brand.
- [ ] **(build)** Web navigation on complex tasks: accessibility API for cross-app navigation, pixel-based clicking for complex web elements (Kuba-Ventures/VALET#258, #259, #260).
- [ ] **(compliance)** Rotate the exposed AssemblyAI eval key and confirm (`PROJECT.md`, What's left item 4).

## Stage 4: Twin Peaks organization and product direction (blocked / not started)

- [ ] ⚠️ **(compliance)** Submit the DUNS request for the Twin Peaks organization (Kuba-Ventures/VALET#336). Owner per issue: Patrick Sanders.
- [ ] ⚠️ **(compliance)** Set up the official Twin Peaks Apple Developer account so DMGs sign under the org identity; blocked on #336 (Kuba-Ventures/VALET#341). Current builds sign as `JAMES FINLEY UNDERWOOD (QZX7VBLDZT)` (`CLAUDE.md`), and changing the signing identity resets users' TCC grants.
- [ ] **(build)** Incorporate Jacques's changes from `Twin-Peaks-Labs/voice-agent` into the app; flagged blocked on @jarnoux (Kuba-Ventures/VALET#335).
- [ ] **(growth)** Evaluate the "Hey Clicky" competitor for functional gaps and email findings (Kuba-Ventures/VALET#337).
- [ ] **(growth)** Observability with PostHog, chosen over LangFuse (Kuba-Ventures/VALET#338). VALET today traces through Langfuse in the proxy (`product-site/lib/proxy/langfuse.ts`).
- [ ] **(growth)** Marketing site with a lead-gen download gate (industry, use case) (Kuba-Ventures/VALET#339). The current `product-site/` gates downloads on a license, not a lead form.
- [ ] **(compliance)** Per-user usage limits, soft cap around $100/month (Kuba-Ventures/VALET#340). See the fair-use flip in Stage 5, which is the existing mechanism.

## Stage 5: Billing and admin (later, deliberately deprioritized per the sponsor mandate)

- [ ] **(compliance)** Turn on fair-use enforcement: code defaults `FAIR_USE_MODE` to `warn` (`product-site/lib/proxy/usage.ts`); set `throttle` or `block` and the per-plan envs in Vercel (mechanism from Kuba-Ventures/VALET#59).
- [ ] **(build)** Confirm account-login PR A (`product-site/app/api/account/app-login`) is deployed so in-app account login works end to end (Kuba-Ventures/VALET#97).
- [ ] **(build)** Device settings Phase 4: conflict resolution (web wins) and poll cadence.
- [ ] **(build)** Raycast console v3: calculator, snippets, clipboard history, window management.
- [ ] **(growth)** `/admin` usage analytics dashboard; needs the "VALET usage, not OS surveillance" privacy decision and a telemetry pipeline first (`coding-plans/backlog.md`).
- [ ] **(compliance)** Stripe payouts paused until a bank account is added (client action).

## Open questions

- Is VALET still the product under active development, or has the work moved to Telly (`Twin-Peaks-Labs/voice-agent`)? No product code has merged here since 2026-07-20, while Telly has shipped continuously through 2026-10-01 and already has PostHog, a lead-gated site and proxy rate limiting, which overlap #338, #339 and #340.
- #336 and #341 are duplicated as Twin-Peaks-Labs/voice-agent#20 and #25. Which repo is the tracker of record?
- Does Vercel `DOWNLOAD_URL` point at the v0.2.44 release? Not checkable from the repo.
- Has `migration_dedupe_licenses.sql` been run in production Supabase?
- What is `FAIR_USE_MODE` actually set to in Vercel?
- Has the AssemblyAI eval key been rotated?
- Should the "open email" intent also launch Mail, as "what's on my calendar" now opens Calendar (`PROJECT.md`, What's left item 12)?
- Stale remote branches (`fix/inbox-summarize-from-rows`, `fix/sports-national-teams`, `chore/release-0.2.44`): merge, close or delete?
