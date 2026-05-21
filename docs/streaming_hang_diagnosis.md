# Streaming hang diagnosis — chunk 16

## What was reported

User log snapshot from a real research run:

```
15:00:48 LLM embedded action: research
15:00:48 research dispatch: routing to native handler
15:00:48 native_research invoked
15:00:49 [voice] Looking into that now, sir
15:00:50 POST /v1/messages 200 OK
15:00:51 POST /v1/messages 200 OK
[SILENCE for 6 minutes — no streaming events, no errors]
15:01:14 [voice] Still gathering, sir
15:02:05 User: [unrelated speech] → Suppressed during research ✓
15:04:08 User: "Alejandro garnacho" → Suppressed during research ✓
15:06:42 User: cancel → Research cancel triggered ✓
15:06:43 [voice] Cancelled, sir
```

Six minutes of dead air after `POST 200 OK`, no errors raised by the
SDK, no content_block events, no progress chip increments. Only the
client-side `Still gathering, sir.` line (the 25s interjection) and
eventually a user-issued cancel.

## What's instrumented now (this chunk)

This chunk is diagnostic only — no fix to the stall itself.

### 1. Per-event INFO logging in the stream loop

Every event the SDK delivers now produces a `stream_event` line in
`logs/jarvis.err.log`:

```
stream_event turn=0 open (model=claude-opus-4-7, msgs=N)
stream_event content_block_start index=I type=T name=N id=ID
stream_event content_block_stop index=I tool_use name=N id=ID input={...}
stream_event content_block_stop index=I (non-tool)
stream_event web_search_tool_result results=N
stream_event web_fetch_tool_result tool_use_id=ID parts=N
stream_event delta_heartbeat count=K (rate=R/s, last_type=T)
stream_event message_stop
stream_event turn=0 closed stop_reason=X in_tokens=N out_tokens=M
```

Deltas are rate-limited to a single heartbeat every 5 seconds with a
rolling count, since they otherwise fire thousands of times per turn.

### 2. 60-second silent-stream watchdog

Replaced `async for event in stream` with a manual iterator wrapped
in `asyncio.wait_for(iterator.__anext__(), timeout=60.0)`. If no
event arrives within 60s the wrapper raises `RuntimeError(
"stream_silent_timeout")` and emits a loud log line:

```
stream_event WATCHDOG: silent for 60s — aborting (turn=N, deltas_so_far=K, search=M, fetch=L)
```

The exception propagates through the existing `try/except` in
`_execute_native_research`, which emits an error panel event and
ends the task cleanly. Without this, the SDK's stream context would
have buffered an indefinitely-idle SSE connection until the 600s
client-level timeout fired, and the user would have seen what they
described: silence, then cancel.

## Findings from a clean baseline run

After instrumenting, ran `tests/smoke_research_routing.py` against
live Opus 4.7. Full output captured at `/tmp/smoke_baseline.log` (274
lines). Key numbers from a successful run on the fishing-poles query:

| metric                         | count                              |
|--------------------------------|------------------------------------|
| total runtime                  | 5m 47s (15:11:52 → 15:17:39)       |
| Opus stream POSTs              | 1 (single SSE for the whole turn)  |
| Haiku voice-summary POST       | 1 (after stream completes)         |
| content_block_start events     | 65+ blocks                         |
| code_execution server_tool_use | 40 invocations                     |
| web_search server_tool_use     | 4                                  |
| web_fetch server_tool_use      | 26                                 |
| input tokens (turn 0)          | 329,125                            |
| output tokens (turn 0)         | 5,941                              |

So a normal completion is **~6 minutes**, not 60–90 seconds as
previously estimated. Dynamic filtering trades latency for accuracy.

### Finding A — `web_fetch` blocks have empty `input={}`

Looking at the actual streamed tool_use blocks, every `web_fetch`
emits with no input:

```
stream_event content_block_stop index=8  tool_use name=web_fetch id=srvtoolu_01EJ8az… input={}
stream_event content_block_stop index=10 tool_use name=web_fetch id=srvtoolu_01JRvW2… input={}
stream_event content_block_stop index=12 tool_use name=web_fetch id=srvtoolu_01UG92V… input={}
…
```

This is because `web_fetch_20260209` (and `web_search_20260209`)
deliver their inputs **through `code_execution`** — the dynamic
filtering documented on the Anthropic side. The actual URL the model
is fetching lives in a Python source string inside the
`code_execution.input.code` field, e.g.

```python
import json
urls = [
    "https://www.outdoorgearlab.com/topics/camping-and-hiking/best-fishing-rod",
    "https://www.wired2fish.com/fishing-videos/best-rod-and-tackle-setups-for-pond-fishing",
    "https://fishingbooker.com/blog/beginner-fishing-rod/",
    "https://www.wired2fish.com/crappie-fishing/choosing-the-right-panfish-rod"
]
for u in urls:
    r = await web_fetch({"url": u})
    …
```

**Consequence:** every panel row labelled "WebFetch" since chunk 8a
has had `detail=""` and `payload.url=""`. The `_emit_research_source_card`
function only fires when `fetch_url_by_id[bid]` is populated, so
**zero `result.research_source` cards have ever rendered** in real
use despite the chunk-8b work that built them. That's a separate
sub-bug surfaced by the instrumentation — flagged here, NOT patched.

Two ways to fix when ready:

  (a) Parse URLs from `code_execution.input.code` (the Python source)
      and emit a synthetic `result.research_source` per URL the model
      passes through `web_fetch(...)`. Brittle but works.

  (b) Drop to `web_fetch_20250910` / `web_search_20250305` (the non-
      dynamic-filtering versions). The model fetches one URL per
      tool call, input is populated normally, our existing panel
      plumbing works. Loses dynamic filtering's accuracy gains.

### Finding B — The two POSTs at 15:00:50 / 15:00:51 are not a retry

In the baseline run, only one Opus stream POST fires per turn, plus
one Haiku POST for the voice summary at the end. So two POSTs in
quick succession during dispatch is unusual.

Best-fit explanation: at the time of the user's hung run, the WS
session had been alive for hours and the `_update_session_summary`
background task fired (`server.py:4031` — gate is
`messages_since_last_summary >= 5 AND len(history) > 20`). That
schedules a Haiku call right around when the next research dispatch
opens its Opus stream. The two POSTs are:

  POST #1 (15:00:50) — background `_update_session_summary` Haiku call
  POST #2 (15:00:51) — Opus stream POST opening (returned 200, then
                       SSE went silent)

This is **not a retry, not a loop, and not a misconfiguration.**
The user can verify by checking whether `messages_since_last_summary`
crossed 5 in the 5 prior turns and `len(history) > 20`; with a
multi-hour session it almost certainly did.

If they want it confirmed beyond a doubt: add `log.info` to
`_update_session_summary` at entry/exit so future double-POST
moments are unambiguous. Not done in this chunk (the user said
diagnose first).

### Finding C — `web_fetch_tool_result.content` is always `parts=0`

When `web_fetch` is wrapped by `code_execution`, the page bodies are
delivered as part of the `code_execution_tool_result`, not the
standalone `web_fetch_tool_result`. So the existing snippet
collector (`tool_result_snippets`) misses all of them in dynamic-
filtering mode. The Haiku middleware still works because Opus's text
output mentions the products with their source URLs explicitly, but
we're feeding the middleware empty result-snippet context. Same
remedy as Finding A.

### Finding D — Long event-free gaps inside `code_execution`

In the baseline, several `code_execution` blocks run for **20–60
seconds** before their `_tool_result` arrives. Examples:

```
15:12:05 content_block_start type=server_tool_use name=code_execution id=srvtoolu_01PKAHMU…
15:12:31 content_block_start type=server_tool_use name=web_fetch    id=srvtoolu_01EJ8azX… ← 26s later
```

```
15:13:33 content_block_start name=code_execution …
15:13:38 content_block_start type=code_execution_tool_result …      ← 5s
```

```
15:13:55 content_block_start name=code_execution …
15:14:14 content_block_start name=web_search …                       ← 19s
```

```
15:14:14 content_block_start name=code_execution …
15:14:44 content_block_start type=code_execution_tool_result …       ← 30s
```

```
15:14:44 content_block_start name=code_execution …
15:15:13 content_block_start …                                       ← 29s
```

```
15:15:13 content_block_start name=code_execution …
15:15:43 content_block_start …                                       ← 30s
```

So **30-second silent windows are normal** under dynamic filtering.
The user's 6-minute window is way past normal — but the underlying
behavior (long silent stretches during code_execution) is *expected*
in this tool family. That's why the 60s watchdog is the right
threshold: tolerate normal code_execution gaps, abort the
pathological multi-minute ones.

## What we still don't know

What actually caused the original 6-minute silence at 15:00:51 is
**still unknown** because that run happened before this chunk's
instrumentation existed. The new logs will catch the next occurrence
unambiguously:

- If `stream_event WATCHDOG: silent for 60s` appears in
  `logs/jarvis.err.log`, the stream's SSE went idle and the abort
  fired correctly. The stall is real and reproducible — at that
  point we know it's an Anthropic-side or network-side issue and
  can decide between (1) shortening the watchdog, (2) adding
  automatic stream-reopen on watchdog fire, or (3) reporting
  upstream.

- If the watchdog never fires across multiple research runs and the
  hang doesn't recur, the original event was either flaky
  network/SSE or an Anthropic-side blip that's since resolved.

## Followup unrelated to this chunk

`logs/jarvis.out.log` shows the following error firing every ~10s
throughout the session:

```
[jarvis.notes] Notes script failed: 324:328: execution error:
Notes got an error: Can't get name of container of note id
"x-coredata://F7A1FEE1-913D-4D6E-8DAA-BBFBAA4C9BFD/ICNote/p209". (-1728)
```

Unrelated to the streaming hang but indicates a broken background
poll in `notes_access.py` that's hammering AppleScript with a stale
note ID. Not fixed in this chunk. Recommend opening a separate
followup to either (a) drop the stale ID once -1728 returns or (b)
back off the polling interval after consecutive failures.

## Verification protocol for the next live test

After restart on this chunk's commit:

1. Run `tail -f logs/jarvis.err.log | grep stream_event` in another
   terminal.
2. Issue any research query. Expect a continuous flow of
   `stream_event` lines: one `turn=0 open`, dozens of
   `content_block_start`, periodic `delta_heartbeat` (every 5s).
3. If the stream hangs:
   - `stream_event WATCHDOG: silent for 60s — aborting (…)` will
     fire 60s into the silent window.
   - The panel will get an error event; the task will end.
   - Now you have the exact last event before silence, which
     identifies whether the hang was inside `code_execution`,
     `web_search`, or `web_fetch` — and we can root-cause from
     there.
