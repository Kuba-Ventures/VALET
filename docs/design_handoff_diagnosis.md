# Design handoff diagnosis (Test 3 failure)

## TL;DR

**Design Panel never activated.** Phase 3 is fully implemented and
reachable from voice, but only via specific trigger verbs. The user's
session used conversational phrasing that doesn't match either the
fast-path regex or the LLM's [ACTION:START_DESIGN] tag rule. The
spawned terminal came from `[ACTION:OPEN_PROJECT]` scaffolding
`.vscode/tasks.json` — that's normal one-shot OPEN_PROJECT behavior,
not a ship-it regression. Ship-it was never reached.

This is a **routing-coverage gap**, not a broken or missing feature.

## Timeline of the failed session (logs/jarvis.err.log)

```
16:37:14  User: open Dharma code
16:37:15  JARVIS: Opening dharma code, sir.
16:37:39  User: Jarvis open Jarvis in cursor
16:37:40  LLM embedded action: {'action': 'open_project', 'target': 'jarvis-main'}
16:37:40  [jarvis.actions] Scaffolded /Users/finley/Code/jarvis-main/.vscode/tasks.json   ← THIS is the terminal
16:37:41  JARVIS: Right away, sir.
16:37:55  User: hey can I ask why you always
16:37:57  JARVIS: Of course, sir — ask away.
16:38:04  User: how come you always make the cursor Windows a full page
16:38:13  JARVIS: I open projects to full Cursor windows because… [conversational answer]
16:38:38  User: yes please add to the code or adjust in the future that you open the
               screen the cursor windows in one quarter of a full screen
16:38:40  LLM embedded action: {'action': 'remember', 'target': 'User prefers Cursor windows…'}
16:38:42  JARVIS: Understood, sir — I'll adjust the launch behavior…
16:39:02  User: great can you add that to the Jarvis main mentioned to Claude that you'd
               like it to open only a quarter window instead of a full page when opening
               cursor
16:39:04  LLM embedded action: {'action': 'prompt_project', 'target': 'jarvis-main ||| Add a note or update the launch configuration…'}
16:39:05  JARVIS: Will do, sir — connecting to jarvis-main now.
```

What fired, in order:
1. `OPEN_PROJECT` — correct interpretation of "open Jarvis in cursor"
2. `REMEMBER` — correct interpretation of "yes please add to the code… in the future"
3. `PROMPT_PROJECT` — correct interpretation of "can you add that to the Jarvis main mentioned to Claude"

What didn't fire: **`START_DESIGN` never appeared in the log.**

## Question-by-question

### 1. Did Design Panel activate?

**No.** Grepping `logs/jarvis.err.log` for the word `design_partner`
across all sessions shows exactly one activation, on **2026-05-17 23:03:40**
during a prior test (`session e0561b05 started on /Users/finley/Code/RecipeBook Code (topic='daily roll up', self_mod=False)`).

In today's failed run (16:37-16:39), zero `design_partner` log lines
appear. The DESIGNING-state branch at `server.py:3884` was never reached
because no design session was associated with the WebSocket. The
conversation flowed entirely through generic Haiku action routing.

### 2. Where is the design-start trigger phrase defined? What activates Design Panel mode today?

Two independent activation paths, both narrow.

**(a) Fast-path regex** at `server.py:2978-2983`:

```python
_START_DESIGN_PATTERN = _action_re.compile(
    r'^\s*(?:let\'?s |let us |i (?:want to|wanna|wish to) |can we |please )?'
    r'(?:design|spec|architect|plan|think through|prototype)\s+'
    r'(?:a |an |the |some )?(?P<topic>[\w .,\'\-]+?)\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)
```

Requires the utterance to contain one of **six explicit verbs**:
`design`, `spec`, `architect`, `plan`, `think through`, `prototype`.
Plus a single-word topic filter (drops `tomorrow`, `today`, `this`,
`that`, `it`, `something` — anti-misroute guard).

The user's transcripts contained **none** of those six verbs. The
fast-path was a no-op for every turn in this session.

**(b) LLM action-tag rule** at `server.py:291-297`:

```
DESIGN-PARTNER MODE (Phase 3):
- [ACTION:START_DESIGN] topic — open a design conversation. JARVIS becomes the
  design partner; subsequent turns route through Opus until the user ships or
  scraps. Use for "let's design X", "plan a Y", "spec a Z", "I want to design
  something for…".
  "let's design a daily rollup" → [ACTION:START_DESIGN] daily rollup
  "plan a feature for client onboarding" → [ACTION:START_DESIGN] client onboarding
```

This is what Haiku reads when routing. The examples are all
`design/plan/spec` framings — same constraint as the regex, just at the
LLM layer. Haiku didn't see any of those keywords in the user's
"hey can I ask why you always…" / "can you add that to the Jarvis main…"
turns, so it (reasonably) routed them as REMEMBER + PROMPT_PROJECT.

Phrases the user actually said vs. what would have activated:

| User said | What fired | What would have activated design |
|---|---|---|
| "hey can I ask why you always" | (conversational) | "let's design X" |
| "yes please add to the code or adjust in the future…" | REMEMBER | "let's design the launch behavior" |
| "great can you add that to the Jarvis main mentioned to Claude…" | PROMPT_PROJECT | "let's plan a feature to make Cursor windows quarter-sized" |

The user's mental model was *starting a discussion about a feature*;
the system's view was *user wants this change made now*. Both are
defensible reads of the words. Neither activation path is wired to
recognize the user's natural feature-discussion phrasing.

### 3. Where did the terminal come from? OPEN_PROJECT or ship-it?

**OPEN_PROJECT.** Trace:

```
16:37:40  LLM embedded action: {'action': 'open_project', 'target': 'jarvis-main'}
16:37:40  [jarvis.actions] Scaffolded /Users/finley/Code/jarvis-main/.vscode/tasks.json
```

`open_project()` at `actions.py:617` calls `_ensure_tasks_json()` at
line 727, which scaffolds a `.vscode/tasks.json` that includes an
auto-run task `claude --dangerously-skip-permissions`. Cursor runs
that task in its integrated terminal as soon as the workspace opens.

This is the documented one-shot OPEN_PROJECT behavior from chunks 1-4.
The terminal opens once when the project is opened; it does **not**
reopen on subsequent voice commands within that project.

**Ship-it never fired.** Grep of today's logs for `ship_via_file`,
`ship_via_applescript`, or `inbox` returns zero hits. Phase 4's
ship-to-`.jarvis/inbox/<id>.md` design at `server.py:1242-1340` was
never invoked because no design session was active to ship.

The user's subsequent `PROMPT_PROJECT` at 16:39:04 ran via
`_execute_prompt_project()` → `WorkSession.send()`, which spawns
`claude -p` as an `asyncio.create_subprocess_exec` pipe — no terminal
window. So the terminal at 16:37:40 is the only one that opened in
this session.

### 4. What's the state of the Design Panel right now?

Fully implemented and wired, end to end:

| Layer | File | Status |
|---|---|---|
| Backend session machine | `design_partner.py` | ✓ DESIGNING/BUILDING states, draft accumulation, Opus loop |
| Action dispatcher | `server.py:1204` (start), `:1242` (ship), `:1477` (scrap), `:1543` (show draft) | ✓ All four handlers wired |
| Voice handler routing | `server.py:3884-3895` (DESIGNING branch bypasses Haiku) | ✓ |
| Fast-path triggers | `server.py:2978-3010` (start regex + ship/scrap/show phrases) | ✓ |
| LLM action-tag rules | `server.py:291-297` (in JARVIS_SYSTEM_PROMPT) | ✓ |
| Frontend panel | `frontend/src/designPanel.{ts,css}`, mounted in `main.ts:77` | ✓ |
| WS protocol | `design_event` type wired in `main.ts:223-226` | ✓ |
| Phase 4 file ship | `design_partner.ship_via_file` writes `<project>/.jarvis/inbox/<id>.md` per design doc | ✓ |
| Live proof | `2026-05-17 23:03:40 design_partner: session e0561b05 started on …RecipeBook Code (topic='daily roll up')` | ✓ activated at least once previously |

Nothing about Phase 3 is half-shipped. The "design conversation" panel
exists and works when invoked. The only failure mode in Test 3 is the
**entry gate**: design activation requires the user to say one of six
specific verbs, and the user used none.

## What's actually broken vs. not broken

| | Status |
|---|---|
| Design Panel implementation | ✓ not broken |
| Design Panel reachability from voice | ✓ reachable via `_START_DESIGN_PATTERN` and `[ACTION:START_DESIGN]` |
| Fast-path coverage of conversational phrasing | ✗ doesn't recognize "I want to talk about", "hey can I ask why…", "can you add…" as design intents |
| LLM action-tag coverage | ✗ JARVIS_SYSTEM_PROMPT only gives Haiku design/plan/spec examples; no guidance for ambient "let's discuss a change" framings |
| OPEN_PROJECT scaffolding | ✓ correct one-shot behavior — terminal opens once on project open |
| Ship-it (Phase 4) | ✓ implementation correct; just unreached in this session |
| Phase 4 ship target | ✓ documented as `<project>/.jarvis/inbox/<id>.md` and that's what `ship_via_file` does (`server.py:1247`, `design_partner.ship_via_file`) — NOT "open a terminal" |

## Recommendation (not patched here)

The fix lands at one of two seams, possibly both. The user picks
which before any code lands.

**Option A — broaden the fast-path regex.** Add verb alternatives
covering ambient design framings:

```python
r'(?:design|spec|architect|plan|think through|prototype|talk about|discuss|brainstorm|figure out|work on|explore|sketch)\s+'
```

Plus phrase prefixes like `"i want to talk about"`, `"let's discuss"`,
`"can we talk about"`. The risk: aggressive matching of "let's discuss
the weather" or "can we talk about lunch" as design topics. Probably
worth a follow-up topic blocklist alongside the existing single-word
filter.

**Option B — teach Haiku to recognize design intent contextually.**
Add to the `JARVIS_SYSTEM_PROMPT` design-mode section: "If the user
asks a 'hey can I ask why you do X' question about Jarvis's behavior
or any open project's behavior, and seems to want it discussed before
deciding to change it, emit [ACTION:START_DESIGN] for the topic.
PROMPT_PROJECT is for one-shot 'just do this' requests; START_DESIGN
is for 'let's think this through first'."

Option B is more robust but harder to evaluate — Haiku will make
judgment calls about which framings are design discussions. Option A
is more deterministic but coarser.

**Option C — explicit user opt-in via a phrase like "design mode".**
Add `"design mode"`, `"talk about a feature"`, `"discuss a change"` as
explicit fast-path triggers with no topic capture. Topic gets prompted
for ("What would you like to design, sir?"). Lowest false-positive
rate, costs the user one extra turn.

I'd lean toward **A+C combined**: broaden the regex modestly (B's
LLM judgment calls are nondeterministic and hard to test), and add
2-3 explicit opt-in phrases for users who learn the system. Avoid
Option B for now — last time we added LLM-judgment routing it took
several rounds to tune (chunks 7→10).

## Out of scope for this diagnosis

- The user's preference ("open Cursor windows at quarter-screen size")
  was correctly captured via REMEMBER + dispatched via PROMPT_PROJECT.
  Whether the actual code change to `actions.py:open_project` lands
  on the user's branch is a separate concern (it's currently a queued
  Claude Code task in jarvis-main).
- Phase 4 ship-it not having been exercised in this session is not a
  bug; the code path is verified by the chunk-4 test plan in
  `docs/design_partner_tests.md` and ran cleanly during phase
  development.
- The `jarvis.notes` `Can't get name of container of note id` spam
  is still firing in `jarvis.out.log` every ~10s. Unrelated; tracked
  in the chunk-16 followup.
