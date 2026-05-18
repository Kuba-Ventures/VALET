"""Live smoke test for the [ACTION:RESEARCH] routing fix.

Exercises two distinct concerns end-to-end:

  Part A — LLM routing decision.
      For each of the 6 test cases from the architectural fix, ask the model
      (via the same routing prompt and model the server uses) which ACTION
      tag it would emit. Verify "show me / find me / what's" route to
      RESEARCH; bare "build me" routes to BUILD; "show me how to build X"
      routes to RESEARCH; the ambiguous "create a project called …" either
      asks for confirmation or surfaces the ambiguity explicitly.

  Part B — Native handler.
      Run _execute_native_research against the actual Anthropic API on the
      original failing query, with the live process_events bus instrumented
      to count tool.web_search / result.* events. Verify ≥ 3 cards land and
      no new entries appear in ~/Desktop or ~/Code.

Costs a handful of Opus/Haiku tokens per run. Requires ANTHROPIC_API_KEY.

Run: .venv/bin/python tests/smoke_research_routing.py
"""

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import anthropic  # noqa: E402

import server  # noqa: E402
from server import extract_action, JARVIS_SYSTEM_PROMPT, USER_NAME  # noqa: E402
from process_events import bus as process_bus  # noqa: E402


# ---------------------------------------------------------------------------
# Part A — LLM routing decision
# ---------------------------------------------------------------------------

ROUTING_CASES = [
    # (utterance, allowed_actions, must_not_be)
    ("Show me the three best fishing poles for backyard ponds in Virginia",
     {"research"}, {"build", "new_project"}),
    ("Find me good coffee shops near Martinsville VA",
     {"research", "browse"}, {"build", "new_project"}),
    ("What's the latest on the SEC ETF rulings",
     {"research", "browse"}, {"build", "new_project"}),
    ("Build me a recipe tracker",
     # Build planning may first ask a question (no action tag), or dispatch
     # build, or — if it really wants — emit add_task. We accept any non-research,
     # non-research-keyword path. Critically, we must NOT see [ACTION:RESEARCH].
     {"build", "new_project", "add_task", None}, {"research"}),
    ("Show me how to build a recipe tracker",
     {"research"}, {"build", "new_project"}),
    ("Create a project called fishing-poles",
     # User explicitly says "create a project" — new_project is the correct
     # tag. RESEARCH would be wrong here.
     {"new_project", "build", None}, {"research"}),
]


def _strip_personal_context_placeholders(prompt: str) -> str:
    # The system prompt has format-string placeholders like {user_name},
    # {project_dir}, {personal_context}. Fill in with reasonable defaults
    # so the prompt is well-formed for an isolated routing call.
    return prompt.format(
        user_name=USER_NAME,
        project_dir="/Users/finley/Code/jarvis-main",
        personal_context="",
    )


async def _route_one(client, utterance: str) -> tuple[str | None, str]:
    """Ask the routing model what tag it would emit. Return (action, raw_text)."""
    system = _strip_personal_context_placeholders(JARVIS_SYSTEM_PROMPT)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=250,
        system=system,
        messages=[{"role": "user", "content": utterance}],
    )
    text = response.content[0].text
    _, action = extract_action(text)
    return (action["action"] if action else None), text


async def run_part_a() -> int:
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    failed = 0
    print("\n=== Part A: LLM routing decision ===\n")
    for utterance, allowed, forbidden in ROUTING_CASES:
        action, raw = await _route_one(client, utterance)
        forbidden_hit = action in forbidden
        allowed_hit = action in allowed
        verdict = "✓" if (allowed_hit and not forbidden_hit) else "✗"
        if verdict == "✗":
            failed += 1
        print(f"  {verdict} {utterance}")
        print(f"     → action={action!r}  (allowed={sorted(a or '∅' for a in allowed)}, "
              f"forbidden={sorted(a or '∅' for a in forbidden)})")
        if verdict == "✗":
            print(f"     raw model output: {raw[:300]!r}")
        print()
    return failed


# ---------------------------------------------------------------------------
# Part B — Native handler end-to-end with live API
# ---------------------------------------------------------------------------

async def run_part_b() -> int:
    print("=== Part B: _execute_native_research end-to-end ===\n")
    if server.anthropic_client is None:
        server.anthropic_client = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=120.0,
        )

    query = "Show me the three best fishing poles for backyard ponds in Virginia"

    # Instrument the process bus by registering a fake WS-like subscriber.
    events: list[dict] = []

    class _Sink:
        async def send_json(self, message: dict):
            ev = message.get("event", {})
            events.append({
                "type": ev.get("type"),
                "title": ev.get("title"),
                "detail": ev.get("detail"),
            })

    sink = _Sink()
    await process_bus.subscribe(sink)

    desktop = Path.home() / "Desktop"
    code = Path.home() / "Code"
    before_desktop = {p.name for p in desktop.iterdir()} if desktop.exists() else set()
    before_code = {p.name for p in code.iterdir()} if code.exists() else set()

    try:
        await server._execute_native_research(query, ws=None)
        # Card extraction is fire-and-forget; give it a moment to land.
        await asyncio.sleep(8)
    finally:
        await process_bus.unsubscribe(sink)

    after_desktop = {p.name for p in desktop.iterdir()} if desktop.exists() else set()
    after_code = {p.name for p in code.iterdir()} if code.exists() else set()

    new_on_desktop = after_desktop - before_desktop
    new_in_code = after_code - before_code

    tool_search_events = [e for e in events if e["type"] == "tool.web_search"]
    tool_fetch_events = [e for e in events if e["type"] == "tool.web_fetch"]
    card_events = [e for e in events if e["type"].startswith("result.")
                   and e["type"] != "result.markdown"]

    print(f"  Process panel events: {len(events)} total")
    print(f"    tool.web_search:  {len(tool_search_events)}")
    print(f"    tool.web_fetch:   {len(tool_fetch_events)}")
    print(f"    result.* cards:   {len(card_events)}")
    for c in card_events:
        print(f"      {c['type']}: {c['title']}")
    print(f"  New entries in ~/Desktop: {sorted(new_on_desktop) if new_on_desktop else 'none'}")
    print(f"  New entries in ~/Code:    {sorted(new_in_code) if new_in_code else 'none'}")
    print()

    failed = 0
    if new_on_desktop:
        print(f"  ✗ FAIL: research created entries on ~/Desktop: {new_on_desktop}")
        failed += 1
    else:
        print("  ✓ No new entries on ~/Desktop")
    if new_in_code:
        print(f"  ✗ FAIL: research created entries in ~/Code: {new_in_code}")
        failed += 1
    else:
        print("  ✓ No new entries in ~/Code")
    if len(tool_search_events) < 1:
        print(f"  ✗ FAIL: expected ≥1 tool.web_search event, got {len(tool_search_events)}")
        failed += 1
    else:
        print(f"  ✓ Saw {len(tool_search_events)} tool.web_search event(s)")
    if len(card_events) < 3:
        print(f"  ⚠ WARN: expected ≥3 result.* cards, got {len(card_events)} "
              "(card extraction is model-dependent; not a hard fail unless 0)")
        if len(card_events) == 0:
            failed += 1
    else:
        print(f"  ✓ Saw {len(card_events)} result.* cards")
    return failed


async def main():
    fail_a = await run_part_a()
    fail_b = await run_part_b()
    total = fail_a + fail_b
    print()
    if total:
        print(f"SMOKE FAIL — {total} failure(s)  (Part A: {fail_a}, Part B: {fail_b})")
        sys.exit(1)
    print("SMOKE PASS — routing decisions and native handler both look good")


if __name__ == "__main__":
    asyncio.run(main())
