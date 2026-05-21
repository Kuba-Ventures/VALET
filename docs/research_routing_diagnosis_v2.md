# Research routing — diagnosis v2

The chunk-7 fix landed on disk but never executed. This document traces why
the user's live test still hit the old subprocess+folder pipeline, what the
process state proves, and what changes have already been made to ensure we
can't be blind to this class of bug again.

No routing-code patches in this round — only diagnostic instrumentation.

## Symptom

Live test of "Show me the three best fishing poles for backyard ponds in
Virginia" against the running JARVIS produced:

- `~/Desktop/find-three-best-fishing/` created on disk
- Process Panel showed a `claude -p` subprocess running, with
  `memory_paths.auto = /Users/finley/.claude/projects/-Users-finley-Desktop-find-three-best-fishing/memory/`
- Three result cards rendered (correct output, wrong path)
- WebSearch tool events streamed in the panel

That memory path slug only forms when Claude Code is spawned with
`cwd=/Users/finley/Desktop/find-three-best-fishing/`. Which only happens if
the old subprocess-and-folder dispatcher fired.

## Process forensics

```
$ ps -o pid,lstart,etime,command -p 29017
  PID  STARTED                  ELAPSED  COMMAND
29017  Mon May 18 00:01:19 2026 11:36:24 .../python server.py
```

Process **start time: 2026-05-18 00:01:19** (12:01:19 AM today).

```
$ git log --format='%h %ai %s'
99c49a7  2026-05-18 11:10:26 -0400  chunk 7: native [ACTION:RESEARCH] …
d19ea43  2026-05-18 00:03:55 -0400  chunk 6: overnight run complete …
55c3870  2026-05-18 00:01:51 -0400  chunk 5: phase 5 self-modification machinery
7a2b1e9  …                            chunk 4: haiku middleware + result cards
```

The running process started **32 seconds before chunk 5 was committed** —
so it loaded chunk 4 source into memory. It has been running through
chunks 5, 6, and 7 without a restart. The chunk-7 fix that landed at
11:10 AM has lived on disk for over 11 hours without ever being loaded.

```
$ stat -f "%Sm" -t "%Y-%m-%dT%H:%M:%S" server.py
2026-05-18T11:36:59   # file mtime (post-fix + post-instrumentation)
```

## Why every symptom is explained by "running chunk-4 code"

Chunk 4 had the slugify → mkdir → `WorkSession.start` → `self_work_and_notify`
→ `claude -p` pipeline. It also had (from chunk 3) the Haiku card-extraction
middleware wired into `WorkSession.send()` and (from chunk 2) the stream-json
parser emitting `tool.web_search` / `tool.web_fetch` panel events from
Claude Code's tool calls. So:

| User-observed symptom              | Explained by                                          |
| ---------------------------------- | ------------------------------------------------------ |
| Folder on Desktop                  | `os.makedirs(~/Desktop/<slug>)` in the chunk-4 path   |
| Memory path with `-Desktop-…` slug | `claude -p` cwd = that folder                         |
| 3 product cards rendered           | `claude_middleware.extract_and_emit` runs at the end of `WorkSession.send()` regardless of how the subprocess was kicked off (chunk 3 wired this universally) |
| WebSearch events in panel          | `work_mode.parse_stream_line` maps Claude Code's `WebSearch` tool_use blocks to `tool.web_search` events (chunk 2) |

There is no rogue second handler. The new dispatcher at `server.py:3535`
is the only `elif embedded_action["action"] == "research":` branch in the
file. The line-3500 hit grep found earlier is the *fallback voice-text*
branch (`response_text = "Looking into that now, sir."`) and never
dispatches anywhere — purely cosmetic.

```
$ grep -n 'research' server.py | grep -v 'system prompt\|comment'
3500:                                    elif action_type == "research":
        # ↑ inside the "if not response_text.strip():" block — sets voice text only
3535:                                elif embedded_action["action"] == "research":
        # ↑ the actual dispatch; this is the corrected native path
```

## Why hypothesis (a) is the entire answer, not (b)

The user's hypothesis (b) — a second code path that still dispatches the
old subprocess — would require **either**:

1. A second handler registered for `embedded_action["action"] == "research"`,
   **or**
2. A fast-path action detector (`detect_action_fast`) that catches research
   utterances and bypasses the LLM dispatcher entirely.

Neither exists. `detect_action_fast` returns `None` for any research-style
phrase (verified by grep — it pattern-matches `merge_branch`, `restart_self`,
and `check_usage` only). The LLM dispatcher at `server.py:3489-3613` is the
single entry point for tagged actions, and the only `"research"` branch
within it is the fix.

So the only remaining explanation is that the dispatcher running in PID
29017 *is not* the dispatcher on disk. The process loaded a stale snapshot
of `server.py` at 12:01 AM and has been holding it in memory since. The
chunk-7 edit to lines 3535-3537 exists only on disk, not in the running
interpreter.

## Loaded-commit visibility — fixed permanently

Diagnosing this took longer than it should have because we had no way to
ask the running process "what code did you load?" Python imports source
once and there is no built-in introspection for it.

To prevent this class of debug ever costing time again, a startup banner
now fires on **every** server boot — emitted at module import (so it
appears under both `python server.py` and `uvicorn server:app`):

```python
def _log_startup_banner() -> None:
    commit = `git rev-parse --short=7 HEAD`
    branch = `git rev-parse --abbrev-ref HEAD`
    dirty  = `git status --porcelain`     # → '+dirty' flag if non-empty
    log.info("[STARTUP] commit=%s%s branch=%s started_at=%s pid=%d",
             commit, dirty_flag, branch, started_at, os.getpid())

_log_startup_banner()
```

Verified by running `python -c "import server"` on the post-edit file:

```
2026-05-18 11:37:15,342 [jarvis] [STARTUP] commit=99c49a7+dirty branch=overnight/2026-05-17 started_at=2026-05-18T11:37:15-04:00 pid=46321
```

From now on the **first** log line on boot reveals exactly what code is
loaded. Grep `jarvis.out.log` for `[STARTUP]` and you have an audit trail
of every restart, what commit it ran, and whether there were uncommitted
edits at the time. The `+dirty` suffix means there's working-tree drift
from the committed snapshot — useful for catching half-applied fixes.

## Native-research instrumentation — also added

Two extra log lines so the next live test produces an unambiguous trace:

- `server.py:_execute_native_research` — first line of the function:
  ```python
  log.info("native_research invoked: query=%r ws=%s", target[:160], "yes" if ws else "no")
  ```
  If the dispatcher fired but the handler bailed before doing anything,
  this still logs.

- `server.py:3535` — at the dispatch site, before `asyncio.create_task`:
  ```python
  log.info("research dispatch: routing to native handler (target=%r)", embedded_action["target"][:160])
  ```
  Confirms the dispatcher branch was even reached.

There are no fallback-to-subprocess branches inside `_execute_native_research`
to add a "bypassed" log to — the handler either runs the Opus tool-use loop
or emits an error event. The closest thing to a "bypass" is the
`if anthropic_client is None` guard at entry, which now logs a
`research bypassed via fallback: reason=no_anthropic_client` warning.

## What to do next

The fix is on disk and tested. The only thing standing between the user
and a working research path is `kill 29017 && python server.py` (or
whatever the project's restart script is). Both the unit tests and the
live smoke test against the Anthropic API passed against the on-disk
code; nothing about the chunk-7 changes is in doubt.

After restart, the **first log line** will read `[STARTUP] commit=99c49a7+dirty branch=overnight/2026-05-17 …` (with `+dirty` only because the instrumentation edits in this round are uncommitted at time of writing). The first `[ACTION:RESEARCH]` query should produce two `[jarvis]` log lines we can grep for:

```
[jarvis] research dispatch: routing to native handler (target='...')
[jarvis] native_research invoked: query='...' ws=yes
```

If those don't show up on the next live test, then — and only then — we
have a second code path to hunt.

## Open recommendation (not done in this round)

The restart-or-reload story for this repo is implicit. There is a
`scripts/restart.sh` referenced by `[ACTION:RESTART_SELF]` that spawns a
detached restarter, but no developer-facing convenience for the case
where a feature lands while the server is still running on old code.

Worth adding: a `make restart` (or `npm run jarvis:restart`) that
`kill`s the running PID by reading a `data/jarvis.pid` file the server
writes on boot, then re-`exec`s. Out of scope for this round; flagging
so it doesn't get lost.
