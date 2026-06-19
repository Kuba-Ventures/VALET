# Backlog — parked ideas, build-ready notes

Deferred features with enough context to pick up cold. Not on the active roadmap
(`voice-mac-control-build-plan.md`) yet.

---

## Usage analytics dashboard for /admin
*Parked 2026-06-19. Reference style: a Rize/RescueTime-like dashboard — "Top apps"
list with trend %, a Keys/Clicks/Scrolls line chart (1d/7d/14d/30d), a Talk-Time
bar chart.*

**Goal:** on `/admin` (the shared-password operator view), see how much users use
VALET and for what — across the user base.

### The two decisions to make FIRST (don't build before these are locked)
1. **What to measure — VALET-usage, NOT OS-surveillance.**
   - ✅ *VALET usage:* commands issued, action types (click / open / browse / type),
     top apps VALET *acted on*, talk time, voice-turn latency, token/API spend.
     On-brand, privacy-defensible, and the data mostly already exists.
   - ❌ *Full OS activity* (the literal screenshot: every app's screen time, all
     keystrokes/clicks/scrolls). That turns VALET into a surveillance tool — a
     different, privacy-loaded product. Steer away.
2. **Privacy / opt-in posture** for sending usage off the user's machine.

### The real work (the charts are the easy 20%)
- **Pipeline gap:** usage today is **local per user** (`_track_usage` →
  `success_tracker.log_usage`; `data/usage_log.jsonl` for token/TTS spend, timestamped;
  voice-turn timing via the latency harness). `/admin` is **central** (Supabase, where
  waitlist/subscribers/MRR/API-spend already live, from #111). To aggregate across users
  you need a **client → Supabase usage-event pipeline** (batched, opt-in, numbers-only).
- Then: aggregation queries + the admin charts (top actions, command volume over time,
  talk time, spend).

### What already exists to build on
- `_track_usage(action_type)` (server.py ~3368) — per-action counts (local, SuccessTracker).
- `data/usage_log.jsonl` + `_append_usage_entry` / `_get_usage_for_period` — token/TTS spend over time.
- Latency harness (`voice_timing`, `/api/latency/last`) — talk-time / perceived latency.
- The #111 `/admin` Supabase store + dashboard — extend it, don't rebuild.

### Recommended next step
**`/office-hours`** to lock the metric set + privacy posture + the local→central pipeline,
THEN build. A fast first cut is possible from existing LOCAL data (action breakdown + token
spend + talk time) as a styled panel, but it'd show only the local user's data until the
central pipeline lands — call that out if shipping it as a teaser.
