# VALET Roadmap: fast, intuitive voice control of the Mac, shipped as a signed, license-gated download

*Owner: Finley · Started: 2026-05-15 (first commit); roadmap added 2026-10-02 (#344) · Status: archived 2026-10-02 and superseded by Telly; v0.2.44 is the last public build; no product code merged since 2026-07-20; wind-down decisions on users and hosted resources still open · last verified against the code 2026-10-02*

> **Note (2026-10-02): VALET is archived and superseded.** This summer the voice-agent work switched to **Telly**, which lives in [`Twin-Peaks-Labs/voice-agent`](https://github.com/Twin-Peaks-Labs/voice-agent) and is live (v0.7.12, shipping via Sparkle). `Kuba-Ventures/VALET` was archived on 2026-10-02. Stages 0 to 2 below are kept as history; the only forward work here is the Wind-down stage.

## Timeline

Oldest first. Dates are PR merge dates, `git log` commit dates, GitHub release and issue dates, or entries in `PROJECT.md` and `CHANGELOG.md`.

- **2026-05-15** · First commit: an executive-assistant baseline, still branded JARVIS, with the process event bus and panel.
- **2026-05-17** · Native research via Opus web tools with streaming source cards (`PROJECT.md` Changelog).
- **2026-05-19** · Voice to Claude Code via auto-paste and dictation (`PROJECT.md` Changelog).
- **2026-06-10** · Phase 2 re-scaffold toward a sellable product: supervised PR factory (#1), license-gated AI/TTS proxy (#9), app routed through it with a license gate (#10), risk-tiered safety (#15, #16), Tauri packaging (#20). Decision: bundled, metered AI, no user API key.
- **2026-06-10** · JARVIS renamed to VALET, persona "Vee"; Marvel branding retired (#14).
- **2026-06-11** · VALET 0.1.0 published: first signed, notarized DMG on `Kuba-Ventures/valet-downloads`. The buy, key, download, install loop was verified end to end.
- **2026-06-11** · Stripe flipped to live under a new Twin Peaks Labs account; Supabase accounts and the self-service portal shipped (#52 to #56).
- **2026-06-12** · Universal Control track (UC1 to UC6) landed on `main`: Accessibility click and type, screen perception, natural-language targeting, the observe-decide-act loop (#80, #87).
- **2026-06-18** · Sponsor mandate from Jacques Arnoux (Twin Peaks): speed is the product, benchmark Clicky, deprioritize billing and admin. Point-and-teach shipped the same day (#113).
- **2026-06-21** · VALET restructured into a menu-bar product: global ⌃⌥ push-to-talk, voice-native Raycast, guided walkthroughs (#135 to #139).
- **2026-06-24** · VALET 0.2.0 published, the first public menu-bar build; 0.2.2 followed the same day.
- **2026-06-25** · Compose-and-stop made the rule for all person-to-person messaging after a wrong-contact bug (#194 to #197).
- **2026-07-05** · Duplicate-license bug fixed at the database layer (#220).
- **2026-07-15** · Live-information layer (sports, markets, news, fast lookup) shipped as 0.2.22, all from keyless sources (#230 to #244).
- **2026-07-17** · `Twin-Peaks-Labs/voice-agent`, the repo where Telly now lives, was created on GitHub. Its history starts from open-source Clicky (first commit 2026-04-08).
- **2026-07-17** · VALET became a Dock app with a new orb icon (0.2.32 to 0.2.34, #273 to #275). The Gmail voice MVP work started (#284).
- **2026-07-20** · Gmail voice MVP done: guided login, inbox digest, save to Apple Notes (#326 to #329). VALET 0.2.44 published (#331, #332). This is the last public build and the last product merge.
- **2026-07-31** · Twin Peaks org issues filed here: DUNS, Apple Developer account, Jacques's changes, PostHog, lead-gen site, usage limits (#335 to #341).
- **2026-08-08** · Only process changes land after July: the shared CLAUDE.md standard (#342) and its no-em-dash rule (#343, 2026-08-09).
- **2026-10-02** · Finley confirmed VALET is superseded by Telly (live at v0.7.12 via Sparkle). Roadmap (#344), code-quality audit (#334) and the wind-down plan (#346) merged.
- **2026-10-02** · #335 to #341 closed as not planned and pointed at Telly (voice-agent #20 and #25).
- **2026-10-02** · `Kuba-Ventures/VALET` archived.

## What this is

VALET was a voice-first macOS menu-bar assistant (British-butler persona "Vee") that hears a held ⌃⌥ push-to-talk chord, answers out loud, and drives the Mac: apps, files, settings, Calendar, Mail (read-only), Notes, the browser, and Claude Code. It is still sold as a license-gated, signed and notarized DMG for Mac builders who want hands-free orchestration. Stack: Python/FastAPI backend (`server.py` and siblings) bundled with PyInstaller, a Vite + TypeScript + Three.js frontend (`frontend/`), a Tauri 2 shell (`src-tauri/`), and a Next.js + Supabase + Stripe license/AI proxy on Vercel (`product-site/`). Public builds are published on `Kuba-Ventures/valet-downloads`.

### Status legend

- [ ] not started · [~] in progress · [x] done
- Tags: **(design)** **(build)** **(growth)** **(compliance)**
- ⚠️ = critical path

---

## Stage 0: Commercial loop and safety foundation (done, 2026-06-10 to 2026-07-05)

- [x] **2026-06-10** · **(build)** License-gated AI/TTS proxy so the app ships with no vendor keys. `product-site/app/api/proxy/{completion,research,tts,usage,v1}`, `product-site/lib/proxy/` (Kuba-Ventures/VALET#9).
- [x] **2026-06-10** · **(build)** App routes all AI and TTS through the proxy; `licensing.py` validates with a 7-day offline grace window (Kuba-Ventures/VALET#10).
- [x] **2026-06-10** · **(build)** Portable control layer and risk-tiered safety (Tier 0 auto, Tier 1 confirm, kill switch). `action_executor.py`, `applescript_executor.py`, `safety.py`, `safe_executor.py` (Kuba-Ventures/VALET#12, #15, #16).
- [x] **2026-06-11** · **(build)** Packaging, Developer ID signing and notarization. `packaging/build-macos.sh`, `packaging/valet.spec` (Kuba-Ventures/VALET#20, #47).
- [x] **2026-06-11** · **(build)** Stripe live, Supabase email/password accounts, account-to-license linking, customer dashboard and owner admin view (Kuba-Ventures/VALET#52 to #56, #54).
- [x] **2026-06-12** · **(build)** Desktop-to-web sync and device settings Phases 1 to 3 (Kuba-Ventures/VALET#56, #57, #66, #68, #69).
- [x] **2026-07-05** · **(compliance)** Duplicate-license fix is on `main`: `upsertLicenseFromSubscription` now upserts with `onConflict: "stripe_subscription_id"` (`product-site/lib/license.ts`), with `product-site/supabase/migration_dedupe_licenses.sql` and `migration_licenses_onconflict_fix.sql`. Whether the migration has been run in production is not recorded (see Open questions) (Kuba-Ventures/VALET#220).
- [x] **date unknown** · **(build)** Branch protection on `main` requires branches to be up to date (`strict: true`) plus the `factory-tests` and `factory-review` checks (`.github/workflows/factory.yml`). Proposed 2026-06-18 in the `PROJECT.md` Decisions log; first recorded as on in #345 (2026-10-02).

## Stage 1: Native Mac control and the menu-bar product (done, shipped since v0.2.0, 2026-06-19 to 2026-06-25)

- [x] **2026-06-19** · **(build)** No-LLM voice console, visible cursor glide with hit-test-guarded click, point-and-teach, latency harness, PTT-primary (Kuba-Ventures/VALET#113 to #123). Plan: `coding-plans/voice-mac-control-build-plan.md`.
- [x] **2026-06-21** · **(build)** Menu-bar shell with global ⌃⌥ chord in the main Tauri process (`spawn_global_chord`, `src-tauri/src/main.rs`) and multi-monitor cursor follower (Kuba-Ventures/VALET#135, #136).
- [x] **2026-06-21** · **(build)** Voice-native Raycast: file, settings and system-action search with no LLM round-trip. `file_index.py`, `settings_index.py`, `system_actions.py` (Kuba-Ventures/VALET#137, #138).
- [x] **2026-06-24** · **(build)** Guided walkthroughs, teach-don't-do, working end to end on-device. `walkthrough.py`, `tests/test_walkthrough.py` (Kuba-Ventures/VALET#139, #167 to #174).
- [x] **2026-06-24** · **(design)** Voice-led violet onboarding and menu-bar UI polish (Kuba-Ventures/VALET#153, #155 to #160, #165).
- [x] **2026-06-25** · **(build)** Compose-and-stop messaging (text, email, Slack) and Apple Clock control: VALET never auto-sends to a person (Kuba-Ventures/VALET#194 to #197).

## Stage 2: Gmail voice MVP and the 0.2.27 to 0.2.44 release train (done, 2026-07-19 to 2026-10-02)

- [x] **2026-07-19** · **(build)** Batch-read a day's Gmail inbox into one spoken digest, from inbox-row previews (Kuba-Ventures/VALET#295 to #303, #307, #308, #312). Opening each email is a recorded non-goal, not a TODO (`PROJECT.md`, Decisions).
- [x] **2026-07-20** · **(build)** Save the last summary to a new Apple Note, structured as per-email cards with action items as Reminders (Kuba-Ventures/VALET#306, #313 to #315, #328).
- [x] **2026-07-20** · **(build)** Scroll actions, "scroll until you find X", scroll a named window (Kuba-Ventures/VALET#317, #319; `tests/test_scroll.py`).
- [x] **2026-07-20** · **(build)** Deepgram STT for the push-to-talk turn only, with fallback to the built-in recognizer (Kuba-Ventures/VALET#320, #325; `deepgram_stt.py`).
- [x] **2026-07-20** · **(build)** Misheard product names corrected before routing (Kuba-Ventures/VALET#323).
- [x] **2026-07-20** · **(build)** Signed-out Gmail recovers through guided login; VALET never types the password (Kuba-Ventures/VALET#326, #327, #329).
- [x] **2026-07-20** · **(build)** v0.2.44 released: `src-tauri/tauri.conf.json` is `0.2.44`, and `Kuba-Ventures/valet-downloads` tag `v0.2.44` is marked Latest (2026-07-20). `PROJECT.md` now records 0.2.44 as the public download (#345) (Kuba-Ventures/VALET#331, #332).

- [x] **2026-10-02** · **(build)** Code-quality audit and removal plan merged as docs only (Kuba-Ventures/VALET#334, merged 2026-10-02; `docs/code-quality-audit.md` is on `main`). It deletes no code; its fix-or-delete items are deferred with the rest of the backlog.

## Stage 3: Wind-down (current, 2026-10-02 to present)

Archive is decided and done (2026-10-02). The remaining calls are Finley's; do not shut anything else off until he decides.

- [x] **2026-10-02** · **(build)** Decide archive vs keep for `Kuba-Ventures/VALET`. Answered: archive (Kuba-Ventures/VALET#346; issue close comments on #335 to #341).
- [ ] **added 2026-10-02** · ⚠️ **(compliance)** Decide what happens to existing VALET users, licenses and Stripe subscriptions (honor, migrate to Telly, or wind down) (Kuba-Ventures/VALET#346).
- [ ] **added 2026-10-02** · **(growth)** Decide the fate of the `Kuba-Ventures/valet-downloads` release page (v0.2.44): keep, point it at Telly, or remove it (Kuba-Ventures/VALET#346).
- [ ] **added 2026-10-02** · **(compliance)** Decide whether to shut off VALET's Vercel project (`valet-voice`), Stripe products and env secrets (Kuba-Ventures/VALET#346).
- [x] **2026-10-02** · **(build)** Close or redirect the Twin Peaks org issues filed here (Kuba-Ventures/VALET#335 to #341). They belong to Telly; DUNS (#336) and the Apple Developer account (#341) are tracked as Twin-Peaks-Labs/voice-agent#20 and #25. All seven closed as not planned on 2026-10-02.
- [ ] **added 2026-10-02** · **(compliance)** Only if VALET stays downloadable: confirm Vercel `DOWNLOAD_URL` points at the v0.2.44 release, and confirm `migration_dedupe_licenses.sql` ran in production Supabase. While it stays up, fair-use enforcement (`FAIR_USE_MODE` defaults to `warn`) and the AssemblyAI key rotation still matter (Kuba-Ventures/VALET#346).

### Not planned while superseded

*Dropped as forward work on 2026-10-02 (#346); each item's added date is in brackets.*

The former Stages 3 to 5 are dropped as forward work: Deepgram proxy gate (#321, added 2026-07-20), UC4/UC5 passes (added 2026-06-12), the `docs/code-quality-audit.md` cleanup (added 2026-10-02), cursor dot recolor (added 2026-10-02), web navigation (#258 to #260, added 2026-07-16), the Twin Peaks product items (#335, #337 to #340, now Telly's; added 2026-07-31, closed not planned 2026-10-02), and billing/admin (fair-use flip, added 2026-06-11; #97 login check, added 2026-10-02; device-settings Phase 4, added 2026-06-11; Raycast v3, added 2026-06-18; `/admin` analytics, added 2026-06-19; Stripe payouts, added 2026-06-11). `PROJECT.md` keeps the detail.

## Open questions

- Archive the repo, or keep it read-only and downloadable? **Answered 2026-10-02: archived** (#346). Whether the download stays up is still the question below.
- What do existing license holders get: continued service, a move to Telly, or a refund or sunset date?
- Should the valet-voice.com site and proxy stay up, and for how long?
- Does Vercel `DOWNLOAD_URL` point at the v0.2.44 release? Only matters if VALET stays downloadable.
- Has `migration_dedupe_licenses.sql` been run in production Supabase? Only matters if VALET stays downloadable.
- Stale remote branches (`fix/inbox-summarize-from-rows`, `fix/sports-national-teams`, `chore/release-0.2.44`): close or delete as part of the wind-down? Still present on 2026-10-02; deleting them needs a temporary unarchive.
