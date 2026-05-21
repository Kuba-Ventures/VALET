# Auto-paste diagnosis (chunk 21 failure)

## TL;DR — one-line bug

`design_partner.get_ship_method()` has a validator that only allows
`"file"` or `"applescript"`. The new `"auto_paste"` value is silently
coerced to `"file"`. The auto-paste branch in `_execute_ship_design`
was never reached, the pre-flight check was never called, and the file
fallback ran every time. Empirically verified — see the smoking gun in
§"What I ran" below.

## Three-question answers

### Q1 — What did the pre-flight check actually see at 17:40:24?

**It was never called.** The auto_paste branch in `_execute_ship_design`
was bypassed entirely because `method != "auto_paste"`.

Evidence in `logs/jarvis.err.log`:

```
17:40:24,325 [jarvis] User: ship it
17:40:24,327 [jarvis.design_partner] ship_via_file: wrote /Users/finley/Code/Dharma Code/.jarvis/inbox/fe5046ad.md
17:40:24,896 [httpx] HTTP Request: POST https://api.fish.audio/v1/tts "HTTP/1.1 200 OK"
```

What would have appeared if pre-flight had run:

```
[jarvis] paste_into_cursor_claude pre-flight failed: frontmost=… (need 'Cursor')
                                                  — or —
[jarvis] paste_into_cursor_claude succeeded (prompt_len=…)
```

Neither line exists in the log for any of the ship-it attempts at
17:39:56 / 17:40:24 / 17:42:37+. `ship_via_file` ran **2ms** after the
"User: ship it" transcript — well under the time even a single
`osascript` invocation needs (~50ms minimum). The auto_paste code path
was not executed.

### Q2 — Is the success-message logic correct?

**The logic is correct, but it never ran on this commit.** Trace from
"ship it" through to TTS for `_execute_ship_design` (`server.py:1394`):

```python
final_prompt = design_partner.compose_final_prompt(session)
method = design_partner.get_ship_method()       # ← returns "file", not "auto_paste"

if method == "auto_paste":                       # ← skipped
    result = await paste_into_cursor_claude(final_prompt)
    if result.get("success"):
        ...
        await _speak(ws, "Sent to Claude Code, sir.")        # ONLY here
        return
    # ... fallback to file with "Prompt staged, sir — <front> was in focus..."

if method == "applescript":                      # ← skipped (was never "applescript")
    ...

# Default: file method                          # ← THIS branch ran
try:
    out = design_partner.ship_via_file(session, final_prompt)
...
await _speak(ws,
    f"Prompt staged at {rel}, sir. Paste it into Cursor's claude terminal to ship."
)
```

The string `"Sent to Claude Code, sir."` is **only** emitted from the
auto_paste-success branch (`server.py:1461`). It is NOT spoken on the
file-fallback path. So if it actually played, the code paths above are
the only place it could come from — and the evidence says that branch
didn't run.

**Subnote on what the user heard:** the actual spoken text from the
file branch on this run would have been:

> "Prompt staged at .jarvis/inbox/fe5046ad.md, sir. Paste it into
> Cursor's claude terminal to ship."

I cannot prove from logs alone whether that's what played — `_speak()`
does NOT log the text it speaks (only the TTS HTTP POST is logged).
The `JARVIS: <text>` log line that normally accompanies a spoken response
**doesn't fire** for `_speak()`-spoken lines. Two possibilities:

  1. User misheard the long staged-fallback line as "Sent to Claude
     Code, sir." — tonally and rhythmically similar enough that this is
     plausible, especially mid-flow. The voice line ends with "...into
     Cursor's claude terminal..." which contains "claude" + a verb-like
     terminal cue.
  2. There's a deeper path that emits "Sent to Claude Code, sir." that
     I haven't found. I searched the codebase for that exact string and
     it appears in ONE place: `server.py:1461` in the auto_paste-success
     branch. No other call site.

Recommend an unrelated followup: add a `log.info("JARVIS: %s", msg)` to
`_speak()` so we can audit spoken text against logs without ambiguity.
Currently the only handler that logs spoken text is the main voice loop
(line ~3961 area); `_speak()` paths bypass that log line entirely.

### Q3 — What is the AppleScript check actually checking?

**It's correct.** `_frontmost_app_name()` queries:

```applescript
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
end tell
return frontApp
```

Empirically verified just now with Cursor open and frontmost:

```
$ osascript -e 'tell application "System Events" to name of first application process whose frontmost is true'
Cursor
$ osascript -e 'tell application "System Events" to bundle identifier of first application process whose frontmost is true'
com.todesktop.230313mzl4w4u92
```

So:

- System Events reports the process NAME, not the bundle ID. The name
  is exactly `"Cursor"` even though the bundle ID is
  `com.todesktop.230313mzl4w4u92` (the ToDesktop builder identifier
  Cursor ships under).
- My check does `frontmost.lower() != "cursor"`. With System Events
  returning `"Cursor"`, that comparison evaluates to `False` (i.e.
  pre-flight would pass).
- Cursor has many helper processes (`Cursor Helper (Renderer)`,
  `Cursor Helper (GPU)`, etc.) but they're never `frontmost` —
  System Events filters those out automatically because helpers don't
  own a UI window.

The check is right. It just never ran.

## What I ran (the smoking gun)

```
$ .venv/bin/python -c "
import sys; sys.path.insert(0, '/Users/finley/Code/jarvis-main')
from design_partner import get_ship_method
from actions import _design_partner_config
cfg = _design_partner_config()
print('config[ship_method]=', repr(cfg.get('ship_method')))
print('get_ship_method()=', repr(get_ship_method()))
"
config[ship_method]= 'auto_paste'
get_ship_method()= 'file'
```

**The config says `auto_paste`. The function returns `file`.** Look at
`design_partner.py:591-599`:

```python
def get_ship_method() -> str:
    """Read configured ship method. Default 'file'. Valid: 'file' | 'applescript'."""
    try:
        from actions import _design_partner_config
        cfg = _design_partner_config()
        m = cfg.get("ship_method", "file")
        return m if m in ("file", "applescript") else "file"      # ← BUG
    except Exception:
        return "file"
```

The whitelist on the last `return` line only allows `"file"` or
`"applescript"`. Any other value — including the new `"auto_paste"` —
falls through to the default `"file"`. Chunk 21 added the auto_paste
branch in `_execute_ship_design` and flipped the config, but never
taught `get_ship_method` about the new value.

This explains every observed symptom:

| Symptom | Explanation |
|---|---|
| `paste_into_cursor_claude` never appeared in logs | The auto_paste branch was bypassed |
| `ship_via_file` ran 2ms after "ship it" | File branch is fast: write file + speak |
| Inbox header shows `ship_method=file` | Because that's what `get_ship_method()` returned |
| Pre-flight check Cursor was wrong about | Pre-flight wasn't even called — see Q1 |

## What needs to land in the fix (not patched yet)

1. **`design_partner.py:597`** — extend the whitelist:
   ```python
   return m if m in ("file", "applescript", "auto_paste") else "file"
   ```
   This is the actual one-line fix.

2. **`design_partner.py:592`** — update the docstring:
   ```python
   """Read configured ship method. Default 'file'.
       Valid: 'file' | 'applescript' | 'auto_paste'."""
   ```

3. **Unit test** that fails until the validator is fixed:
   ```python
   def test_get_ship_method_accepts_auto_paste():
       # Patch the cached config to contain 'auto_paste' and assert
       # get_ship_method() returns it verbatim.
   ```
   This would have caught the bug in chunk 21 if I'd thought to write it.

4. **Recommended followup, unrelated to fix:** `_speak()` should
   `log.info("JARVIS: %s", msg)` so spoken text appears in the log
   alongside the TTS HTTP POST. Without it, we can't independently
   verify what played on the user's speakers — only that *something*
   played. Made this diagnosis harder than it needed to be.

## What the user heard, best guess

Given the config bug + the actual file-branch voice line, the most
likely scenario is:

  - Spoke: "Prompt staged at .jarvis/inbox/fe5046ad.md, sir. Paste it
    into Cursor's claude terminal to ship."
  - User heard / remembered as: "Sent to Claude Code, sir."

The two lines share the cadence (~3-4 phrases, ends with "...sir") and
both contain "claude" / "Claude Code"-adjacent words. After a long day
of testing, easy to compress in memory.

But the user said they were **certain**. The (4) recommendation above
(log `_speak` text) would let us settle this definitively next time.

Until that lands, the diagnosis is: the code paths I can prove from
logs are inconsistent with "Sent to Claude Code, sir." actually being
spoken. The auto_paste branch did not run. If you want to verify the
spoken text empirically before the fix, the cheap test is: restart the
server with `_speak` logging added, run ship-it once, grep the log
for the spoken text.

## Why all 19 unit tests passed despite this bug

The tests covered:
  - `detect_action_fast` routing (text → intent)
  - `_extract_urls_from_code` parsing
  - Middleware enrichment + currency strip
  - System-prompt content

**None of them exercised `get_ship_method`** or the
`_execute_ship_design` branch dispatch. The bug lives in a code path
the test suite never touched. Adding the test in (3) above closes the
gap.
