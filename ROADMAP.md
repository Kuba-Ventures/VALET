# VALET Roadmap: fast, intuitive voice control of the Mac, shipped as a signed, license-gated download

*Owner: Finley · Started: 2026-10-02 · Status: superseded by Telly; v0.2.44 is the last public build; no product code merged since 2026-07-20; in wind-down · last verified against the code 2026-10-02*

> **Note (2026-10-02): VALET is superseded.** This summer the voice-agent work switched to **Telly**, which lives in [`Twin-Peaks-Labs/voice-agent`](https://github.com/Twin-Peaks-Labs/voice-agent) and is live (v0.7.12, shipping via Sparkle). VALET is no longer actively developed. Stages 0 to 2 below are kept as history; the only forward work here is the Wind-down stage.

## What this is

VALET was a voice-first macOS menu-bar assistant (British-butler persona "Vee") that hears a held ⌃⌥ push-to-talk chord, answers out loud, and drives the Mac: apps, files, settings, Calendar, Mail (read-only), Notes, the browser, and Claude Code. It is still sold as a license-gated, signed and notarized DMG for Mac builders who want hands-free orchestration. Stack: Python/FastAPI backend (`server.py` and siblings) bundled with PyInstaller, a Vite + TypeScript + Three.js frontend (`frontend/`), a Tauri 2 shell (`src-tauri/`), and a Next.js + Supabase + Stripe license/AI proxy on Vercel (`product-site/`). Public builds are published on `Kuba-Ventures/valet-downloads`.

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
- [x] **(build)** v0.2.44 released: `src-tauri/tauri.conf.json` is `0.2.44`, and `Kuba-Ventures/valet-downloads` tag `v0.2.44` is marked Latest (2026-07-20). `PROJECT.md` now records 0.2.44 as the public download (#345).

- [x] **(build)** Code-quality audit and removal plan merged as docs only (Kuba-Ventures/VALET#334, merged 2026-10-02; `docs/code-quality-audit.md` is on `main`). It deletes no code; its fix-or-delete items are deferred with the rest of the backlog.

## Stage 3: Wind-down (current)

Nothing is decided yet. Do not archive or shut anything off until Finley decides.

- [ ] ⚠️ **(build)** Decide archive vs keep for `Kuba-Ventures/VALET`.
- [ ] ⚠️ **(compliance)** Decide what happens to existing VALET users, licenses and Stripe subscriptions (honor, migrate to Telly, or wind down).
- [ ] **(growth)** Decide the fate of the `Kuba-Ventures/valet-downloads` release page (v0.2.44): keep, point it at Telly, or remove it.
- [ ] **(compliance)** Decide whether to shut off VALET's Vercel project (`valet-voice`), Stripe products and env secrets.
- [ ] **(build)** Close or redirect the Twin Peaks org issues filed here (Kuba-Ventures/VALET#335 to #341). They belong to Telly; DUNS (#336) and the Apple Developer account (#341) are tracked as Twin-Peaks-Labs/voice-agent#20 and #25.
- [ ] **(compliance)** Only if VALET stays downloadable: confirm Vercel `DOWNLOAD_URL` points at the v0.2.44 release, and confirm `migration_dedupe_licenses.sql` ran in production Supabase. While it stays up, fair-use enforcement (`FAIR_USE_MODE` defaults to `warn`) and the AssemblyAI key rotation still matter.

### Not planned while superseded

The former Stages 3 to 5 are dropped as forward work: Deepgram proxy gate (#321), UC4/UC5 passes, the `docs/code-quality-audit.md` cleanup, cursor dot recolor, web navigation (#258 to #260), the Twin Peaks product items (#335, #337 to #340, now Telly's), and billing/admin (fair-use flip, #97 login check, device-settings Phase 4, Raycast v3, `/admin` analytics, Stripe payouts). `PROJECT.md` keeps the detail.

## Open questions

- Archive the repo, or keep it read-only and downloadable?
- What do existing license holders get: continued service, a move to Telly, or a refund or sunset date?
- Should the valet-voice.com site and proxy stay up, and for how long?
- Does Vercel `DOWNLOAD_URL` point at the v0.2.44 release? Only matters if VALET stays downloadable.
- Has `migration_dedupe_licenses.sql` been run in production Supabase? Only matters if VALET stays downloadable.
- Stale remote branches (`fix/inbox-summarize-from-rows`, `fix/sports-national-teams`, `chore/release-0.2.44`): close or delete as part of the wind-down?
