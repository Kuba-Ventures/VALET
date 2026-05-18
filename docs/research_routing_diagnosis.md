# Research routing diagnosis

Scope: the "show me the three best fishing poles" query incorrectly created a
folder on the Desktop and dispatched a Claude Code subprocess. This document
traces the exact code path, identifies why a folder name was derived, and
audits what's already in place so the fix is build-not-design work.

No code changes here — diagnosis only.

## 1. Live `[ACTION:RESEARCH]` path

The system prompt at `server.py:203` instructs the LLM:

> `[ACTION:RESEARCH] detailed research brief — when user wants real research
> with real data. Claude Code will browse the web, find real listings/data,
> and create a report document. Give it a detailed brief of what to find.`

So a research-style utterance produces an `[ACTION:RESEARCH] …` tag from the
conversational Haiku/Opus turn. `extract_action()` at `server.py:844` parses
it into `{"action": "research", "target": "<brief>"}`.

The live dispatcher branch is `server.py:3529-3537`:

```python
elif embedded_action["action"] == "research":
    # Research enters work mode too
    name = _generate_project_name(embedded_action["target"])
    path = str(Path.home() / "Desktop" / name)
    os.makedirs(path, exist_ok=True)
    await work_session.start(path)
    asyncio.create_task(
        self_work_and_notify(work_session, embedded_action["target"], ws)
    )
```

This:
1. Slugifies the brief into a folder name.
2. Creates `~/Desktop/<slug>/` on disk.
3. Activates the persistent `WorkSession` against that folder.
4. Spawns `self_work_and_notify` → `WorkSession.send()` → `claude -p
   --output-format stream-json --verbose --dangerously-skip-permissions` in
   that folder (`work_mode.py:202-226`).

That subprocess is what runs `WebSearch` / `WebFetch` and surfaces them as
`tool.web_search` / `tool.web_fetch` panel events (`work_mode.py:60-70`).

### Dead code worth noting
- `_execute_research` at `server.py:1657` — defined, never referenced. Older
  variant of the same subprocess-and-folder pattern.
- `handle_research` at `server.py:3014` — defined, never referenced. The
  *only* place in the repo that calls Opus directly with `model=claude-opus-4-6`
  for research and writes an HTML file. No tool use; pure prose generation.

Both are vestigial. Removing them is part of the cleanup; nothing routes
through them today.

## 2. Where the folder name comes from

`_generate_project_name` lives in `actions.py:1202-1230`. Rules, in order:

1. If the user's text contains a `"quoted string"`, use that as the slug.
2. If the text contains `called X` or `named X`, use `X`.
3. Otherwise: lowercase, strip punctuation, drop a stopword list (`a`, `the`,
   `build`, `create`, `make`, `for`, `with`, `web`, `page`, `site`, …),
   take the first up to four "meaningful" words, join with `-`.

For the prompt `"show me the three best fishing poles for backyard ponds in
Virginia"` the stopword list eats `show`, `me`, `the`, `for`, `in`, but
`three`, `best`, `fishing`, `poles` survive in that order — first four wins,
producing `three-best-fishing-poles` or similar (close to the reported
`find-three-best-fishing`; the exact slug depends on which version of the
Haiku-generated `target` was passed in, but the mechanism is the same).

The function was designed for build prompts ("build me a recipe tracker" →
`recipe-tracker`). It has no guard against research-style inputs because the
caller (`server.py:3531`) unconditionally treats `[ACTION:RESEARCH]` as a
work-mode dispatch that needs a cwd.

The fix has to land at the *caller* (don't slugify for research), not in
`_generate_project_name`.

## 3. Where Opus already uses Anthropic tool-use natively

Audit of `tools=` and `tool_use` references across the codebase:

| Location                       | Tool-use kind                          | Web tools? |
|--------------------------------|----------------------------------------|------------|
| `design_partner.py:334`        | `tools=[_DESIGN_TOOL]`, forced-use     | No — structured output via tool-forcing for the design partner state machine. |
| `work_mode.py:60-70`           | Parses `claude -p` stream-json output  | Yes, but those tools are Claude Code's first-party `WebSearch`/`WebFetch` running inside the subprocess — not Anthropic-API tool use. |
| All other `client.messages.create(...)` sites (`server.py:749, 1873, 1949, 2114, 3017, 3053, 3086, 3339, 3963`) | Plain chat, no `tools=` parameter      | No.        |

**Conclusion:** Opus does **not** currently have native `web_search` /
`web_fetch` access via the Anthropic API. Every web tool call today flows
through the Claude Code subprocess. The "two-brain routing" (Haiku for
conversation, Opus for harder turns) is currently text-only on both sides.

The fix needs to introduce Anthropic-API tool use at the JARVIS process
level. The shape is well-supported by `anthropic.AsyncAnthropic` (already
imported at `server.py:55`); we'd pass `tools=[{"type": "web_search_…", ...},
{"type": "web_fetch_…", ...}]` and loop on `stop_reason == "tool_use"` until
the model is done. The model needs to be one that supports server-side
search tools — `claude-opus-4-6` / `claude-opus-4-7` / `claude-sonnet-4-6`
do. (Knowledge cutoff caveat: confirm the exact tool-type strings against
the current Anthropic docs at implementation time; do not hardcode from
memory.)

## 4. Does the Haiku middleware work on a direct Opus response?

`claude_middleware.extract_and_emit` (`claude_middleware.py:106-203`) takes:

- `response_text: str` — the assistant's final reply (concatenated text)
- `tool_result_snippets: list[str]` — short snippets from tool_result blocks
- `task_id: str`
- `anthropic_client: Any`

The function body has **no dependency** on stream-json, on the `claude` CLI,
or on the `WorkSession` object. It runs a single Haiku messages.create with
a fixed extraction system prompt and emits `result.<kind>` events on the
shared `process_events.bus`. Tolerant-by-design failure handling
(`claude_middleware.py:11-17`).

The only stylistic tell is the extraction system prompt at line 61:

> `You extract structured "result cards" from a Claude Code response.`

That copy mentions Claude Code, but the Haiku model is fed plain text and
will not behave differently if the source is an Opus response. We can either
leave it (no functional impact) or generalize it ("from an assistant
response") when we wire the new path.

**The middleware is reusable as-is.** The work on the new handler is purely
"gather `response_text` + `tool_result_snippets` from the Opus loop and call
`extract_and_emit`." All the Pydantic schema, JSON parsing, fence-stripping,
and result.* emission logic stays exactly the same. The frontend Process
Panel already auto-pins on the first `result.*` card (`processPanel.ts:247-250`)
and renders `result.web` / `result.product` / `result.location` / `result.image`
(`processPanel.ts:45-49`).

## 5. Surface decisions for the build phase

Things the diagnosis surfaces that need a call before coding starts:

1. **Action tag name.** Reuse `[ACTION:RESEARCH]` (the system prompt already
   trains the model to emit it) and just rewire its handler. No new tag.
2. **Model for the native research turn.** Opus 4.7 is the default
   recommendation per the environment notes; Sonnet 4.6 is the cheaper
   fallback. Both support server-side web tools.
3. **Cleanup of dead `_execute_research` and `handle_research`.** Remove
   when the new handler lands so future readers don't re-introduce a
   subprocess path.
4. **Stopword list / slugifier.** Leave `_generate_project_name` alone —
   it's still correct for build/new_project. The bug is the *caller* using
   it for research.
5. **System prompt routing rule.** The current line 203 description ("Claude
   Code will browse the web … create a report document") needs to change:
   no mention of folders, reports, or files. Pair it with a new explicit
   distinction between "research" intent and "build/new_project" intent —
   currently the only build hint is in the BUILD PLANNING block at
   `server.py:139-144`, which is about asking clarifying questions rather
   than disambiguating intent.
6. **Ambiguous case ("Create a project called fishing-poles").** The user's
   prompt asks us to document the choice. Recommendation: confirm via voice
   ("Project or research?") on `[ACTION:NEW_PROJECT]` only when the brief
   is short and ambiguous, otherwise proceed. But this is a downstream
   policy question, not part of fixing the routing.
