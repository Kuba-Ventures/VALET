# Research routing — diagnosis v3 (issue #5 audit)

## What #5 claims to track

User report from chunk-10 follow-up: "Jarvis sometimes gets lost during
long research waits." The user hypothesized this is downstream of #4
(background-noise interrupts) and asked for a code audit of four
specific failure modes after #4 lands.

## Audit results

### A. Research task started but never completed

**Status: fully resolved by chunk 11 (#4).**

The previous failure mode was: ambient transcripts arrived during a
running research turn, dispatched to the LLM, which sometimes left
the original research task orphaned (no completion event, no error
event, panel stuck "active"). The transcript intercept in chunk 11
discards all non-cancel transcripts during research, so the original
task is no longer interrupted by side traffic.

Belt-and-suspenders fix already in place: `task_context` in
`process_events.py` now catches `asyncio.CancelledError` separately
from `Exception` and emits `task_done` with status=done. Previously
CancelledError (a BaseException) wasn't caught at all, leaving the
panel's `activeTaskCount` stuck > 0.

Verification on live: trigger research, say "stop" mid-run, confirm
`task_done` event fires (panel stream shows the task transition).

### B. Cards from different runs mixing

**Status: by design under the chunk-10 spec, but worth re-examining.**

Floating result cards persist across research runs — the chunk-10
spec was explicit ("Don't keep ghost state — just unmount" applied
only to the per-card X button, not to new-run boundaries). So if
the user runs query A, gets 3 product cards, doesn't dismiss them,
then runs query B, they'll see query A's cards alongside query B's.

The grid logic in chunk 12 handles this by filling next-free-slot
without reshuffling existing cards. There is no DOM mixing or state
leak — each card has its own `data-task-id` matching its originating
research run, and the cascade resets per task so slot 0 is reused.

**Open UX question, not a bug:** should `task_start` auto-clear the
previous run's cards from the layer? The current behavior matches
the chunk-10 spec. If you want a different behavior ("each run is
disposable, dismiss the prior batch automatically"), that's a
deliberate change, not a fix.

### C. Orb stuck on "still gathering, sir." forever

**Status: latent issue surfaces only if the voice summary fails.**

The "Still gathering, sir." line is the 25s mid-research voice
interjection from chunk 8c. After research completes, the final
voice summary runs and overrides the user's last-spoken audio
memory. If the summary fails to synthesize or send:

  server.py:_execute_native_research → voice summary block
  └─ if anthropic_client.messages.create fails:
       msg = summary.content[0].text     # AttributeError
     └─ wrapped in try/except Exception: log.warning(…)
  └─ if synthesize_speech returns None: silently no audio
  └─ if ws.send_json fails: silently swallowed

In each fallthrough, no follow-up audio plays; "Still gathering"
is the last thing the user hears. That's the "orb stuck" symptom.

**This is a real latent issue, not fixed by #4.** Two options for
when this matters:

  1. Detect and re-speak: if the voice summary path silently
     fails, fall back to a generic "Research complete, sir."
     line so the user gets closure.

  2. Surface only — leave the silence in place but make
     `logs/jarvis.err.log` surface the failure visibly so the
     user can grep for it after the fact.

Surfacing (option 2) only — flagged here, NOT patched. Per the
user's directive: "If something else is going on … diagnose and
surface separately. Don't patch silently."

### D. Process Panel showing stale events from a previous run

**Status: real issue surfaced — the panel doesn't reset its stream
between research runs when the user is keeping floating cards.**

Walkthrough:

  Run 1: research starts → panel appears → events stream → research
  completes → final voice line → floating cards spawn.

  Auto-dismiss is gated on `cardCount() > 0` — so if the user keeps
  the cards visible (doesn't X them), the panel never dismisses.
  The panel's stream still holds: 2 tool.web_search rows, 4-5
  tool.web_fetch rows, the "Research complete" step, possibly the
  "Full response" markdown details block.

  Run 2: user issues a new research query. `task_start` fires →
  panel's `handleEvent` increments `activeTaskCount` and inserts a
  new task_start row at top of stream — but the old run's rows
  are still there underneath.

This is correctly a "stale events" leak between runs. The panel
should clear its event stream on `task_start` when a fresh task
arrives (not its floating cards, which are independent), so each
research session has a clean event log.

**Not fixed by #4.** Flagged here, NOT patched. Same surfacing-only
policy.

## What's actually fixed by #4 alone

- (A) orphan task completion ✓
- background noise interrupting research ✓
- cancel words wired up ✓

## What's NOT fixed by #4

- (B) is a UX choice from chunk 10, not a bug — verify with the user
  whether they want different new-run semantics before changing
- (C) voice summary failure → silent "Still gathering, sir." last
  state — latent, can land as a one-line fallback if needed
- (D) panel stream leaks between runs when the user keeps cards —
  one-line fix in `task_start` handler if the user wants the
  stream to reset

## Live verification protocol

After deploying #1-4, the user should run:

1. Quiet env, research → confirm full sequence works.
2. **TV-on test:** start research, play a podcast or TV next to the
   mic for 60s. Confirm:
   - `logs/jarvis.err.log` shows `Suppressed during research`
     entries for each ambient utterance.
   - Panel keeps progressing (chip updates, source cards spawn).
   - No `User: …` line gets routed to the LLM during the window.
3. **Cancel test:** start research, say "cancel" at ~15s. Confirm
   "Cancelled, sir." audio plays within 2s, panel transitions to
   `task_done`, no late audio surprises.
4. **Sequential runs test:** run query A, leave cards on screen,
   immediately run query B. **Expect a known issue:** old panel
   events are still visible (per audit point D). User decides
   whether that's worth a follow-up patch.

If any of (1)-(3) fail in ways NOT predicted by this doc, that's
a separate bug — diagnose freshly.
