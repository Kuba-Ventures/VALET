"""Unit tests for the [ACTION:RESEARCH] routing fix.

Covers the six cases the architectural fix is supposed to handle. The first
three and the disk-safety guarantees are testable here without a live LLM.
The last three (cases #4–#6) depend on the system prompt's intent
disambiguation, which is enforced by the LLM at runtime — we can't
unit-test the LLM's choice, so for those cases we assert the system prompt
itself contains the rules that govern the choice. A live smoke test (run
manually after starting the server) closes the loop on the LLM-decision
cases.

Run: python3 tests/test_research_routing.py
"""

import asyncio
import json as _json
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env if present so server imports succeed.
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import server  # noqa: E402
from server import extract_action, JARVIS_SYSTEM_PROMPT  # noqa: E402


# ---------------------------------------------------------------------------
# 1. extract_action parses [ACTION:RESEARCH] without slugifying
# ---------------------------------------------------------------------------

def test_extract_action_research():
    text, action = extract_action(
        "Looking into that now, sir. [ACTION:RESEARCH] three best fishing poles for backyard ponds in Virginia"
    )
    assert action is not None
    assert action["action"] == "research"
    assert action["target"] == "three best fishing poles for backyard ponds in Virginia"
    # Speakable preamble is preserved; tag is stripped.
    assert "Looking into that now" in text
    assert "[ACTION" not in text
    print("✓ extract_action parses [ACTION:RESEARCH] correctly")


def test_extract_action_distinguishes_research_from_build():
    # Build target should not be parsed as research.
    _, action = extract_action("[ACTION:BUILD] a snake game")
    assert action["action"] == "build"
    # Research target should not be parsed as build.
    _, action = extract_action("[ACTION:RESEARCH] best running shoes")
    assert action["action"] == "research"
    print("✓ extract_action distinguishes research from build tags")


# ---------------------------------------------------------------------------
# 2. Disk-safety: _execute_native_research never writes to ~/Desktop or ~/Code
# ---------------------------------------------------------------------------

class _FakeBlock:
    """Minimal stand-in for an Anthropic content block."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeUsage:
    input_tokens = 100
    output_tokens = 200
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _FakeResponse:
    """Mimics anthropic.types.Message enough for the handler."""
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()
        self.model = "claude-opus-4-7"


def _build_fake_research_response():
    """A canned Opus reply with one server_tool_use + one tool_result + text."""
    return _FakeResponse(content=[
        _FakeBlock(
            type="server_tool_use",
            id="srvtoolu_test1",
            name="web_search",
            input={"query": "three best fishing poles backyard ponds Virginia"},
        ),
        _FakeBlock(
            type="web_search_tool_result",
            tool_use_id="srvtoolu_test1",
            content=[
                _FakeBlock(
                    type="web_search_result",
                    url="https://example.com/poles",
                    title="Best Backyard Pond Fishing Rods",
                ),
            ],
        ),
        _FakeBlock(
            type="text",
            text=(
                "Three rods worth considering, sir: the Ugly Stik GX2 at $39, "
                "the Shakespeare Ugly Stik Elite at $52, and the Daiwa BG at $89. "
                "All work well for crappie and bass in small ponds."
            ),
        ),
    ])


class _FakeStream:
    """Mimics the AsyncMessagesStream context manager + async iterator."""
    def __init__(self, events, final_message):
        self._events = list(events)
        self._final = final_message
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._idx]
        self._idx += 1
        return ev

    async def get_final_message(self):
        return self._final


def _build_fake_stream_events_and_final():
    """Synthesize the stream events the SDK would emit for the canned reply
    above — content_block_{start,delta,stop} interleaved across indices."""
    final = _build_fake_research_response()
    tool_input_json = _json.dumps({"query": "three best fishing poles backyard ponds Virginia"})

    events = [
        # Block 0 — server_tool_use (web_search)
        _FakeBlock(
            type="content_block_start",
            index=0,
            content_block=_FakeBlock(
                type="server_tool_use",
                id="srvtoolu_test1",
                name="web_search",
            ),
        ),
        _FakeBlock(
            type="content_block_delta",
            index=0,
            delta=_FakeBlock(type="input_json_delta", partial_json=tool_input_json),
        ),
        _FakeBlock(type="content_block_stop", index=0),

        # Block 1 — web_search_tool_result (delivered whole at block_start)
        _FakeBlock(
            type="content_block_start",
            index=1,
            content_block=_FakeBlock(
                type="web_search_tool_result",
                tool_use_id="srvtoolu_test1",
                content=[
                    _FakeBlock(
                        type="web_search_result",
                        url="https://example.com/poles",
                        title="Best Backyard Pond Fishing Rods",
                    ),
                ],
            ),
        ),
        _FakeBlock(type="content_block_stop", index=1),

        # Block 2 — text (streamed via text_delta)
        _FakeBlock(
            type="content_block_start",
            index=2,
            content_block=_FakeBlock(type="text", text=""),
        ),
        _FakeBlock(
            type="content_block_delta",
            index=2,
            delta=_FakeBlock(
                type="text_delta",
                text=(
                    "Three rods worth considering, sir: the Ugly Stik GX2 at $39, "
                    "the Shakespeare Ugly Stik Elite at $52, and the Daiwa BG at $89. "
                    "All work well for crappie and bass in small ponds."
                ),
            ),
        ),
        _FakeBlock(type="content_block_stop", index=2),

        _FakeBlock(type="message_stop"),
    ]
    return events, final


def _make_fake_client_with_stream():
    """Construct a fake AsyncAnthropic-like client with messages.stream wired up."""
    fake = MagicMock()
    fake.messages = MagicMock()

    events, final = _build_fake_stream_events_and_final()
    fake.messages.stream = MagicMock(return_value=_FakeStream(events, final))
    # The Haiku voice summary call still uses messages.create; return a stub
    # so the handler doesn't crash when summarizing.
    summary_response = _FakeResponse(content=[
        _FakeBlock(type="text", text="Three rods worth considering, sir.")
    ])
    fake.messages.create = AsyncMock(return_value=summary_response)
    # with_options(...) returns a (lightly modified) clone — for tests, just
    # return self so the streaming call lands on this same mock.
    fake.with_options = MagicMock(return_value=fake)
    return fake


def _snapshot_dir(p: Path) -> set:
    if not p.exists():
        return set()
    return {child.name for child in p.iterdir()}


def test_native_research_does_not_touch_disk():
    """The single most load-bearing test: research must not create folders."""
    desktop = Path.home() / "Desktop"
    code = Path.home() / "Code"

    before_desktop = _snapshot_dir(desktop)
    before_code = _snapshot_dir(code)

    fake_client = _make_fake_client_with_stream()

    original_client = server.anthropic_client
    server.anthropic_client = fake_client
    try:
        asyncio.run(server._execute_native_research(
            "three best fishing poles for backyard ponds in Virginia",
            ws=None,
        ))
    finally:
        server.anthropic_client = original_client

    after_desktop = _snapshot_dir(desktop)
    after_code = _snapshot_dir(code)

    new_on_desktop = after_desktop - before_desktop
    new_in_code = after_code - before_code

    assert not new_on_desktop, (
        f"Research path created entries on ~/Desktop: {new_on_desktop}. "
        "Research must NEVER touch the filesystem."
    )
    assert not new_in_code, (
        f"Research path created entries in ~/Code: {new_in_code}. "
        "Research must NEVER touch the filesystem."
    )
    print("✓ _execute_native_research does not write to ~/Desktop or ~/Code")


def test_native_research_uses_opus_47_and_web_tools():
    """Confirms the new path actually calls Opus 4.7 with the right tools, via streaming."""
    fake_client = _make_fake_client_with_stream()

    original_client = server.anthropic_client
    server.anthropic_client = fake_client
    try:
        asyncio.run(server._execute_native_research("good coffee near Martinsville VA", ws=None))
    finally:
        server.anthropic_client = original_client

    # Must use streaming, not create — the timeout fix is the whole point.
    assert fake_client.messages.stream.call_count >= 1, (
        "Native research should call messages.stream(...) — non-streamed requests "
        "time out on long server-side tool loops."
    )
    call_args = fake_client.messages.stream.call_args_list[0]
    kwargs = call_args.kwargs

    assert kwargs["model"] == "claude-opus-4-7"
    tool_types = sorted(t["type"] for t in kwargs["tools"])
    assert tool_types == ["web_fetch_20260209", "web_search_20260209"]
    tool_names = sorted(t["name"] for t in kwargs["tools"])
    assert tool_names == ["web_fetch", "web_search"]

    # Confirm the extended timeout was requested via with_options.
    assert fake_client.with_options.call_count >= 1, (
        "Research should call client.with_options(timeout=...) for the 10-min cap."
    )
    options_kwargs = fake_client.with_options.call_args_list[0].kwargs
    assert options_kwargs.get("timeout", 0) >= 60, (
        f"Research timeout should be ≥60s for long-running tool loops; got {options_kwargs}"
    )
    print("✓ Native research uses streaming + claude-opus-4-7 + web tools + extended timeout")


# ---------------------------------------------------------------------------
# 3. System prompt enforces the routing rules at LLM-decision time
# ---------------------------------------------------------------------------

def test_system_prompt_drops_report_document_language():
    """The old description told the model it was creating a report. Confirm
    every assertive (non-negated) form of that instruction is gone."""
    research_section = _extract_research_section(JARVIS_SYSTEM_PROMPT)
    lowered = research_section.lower()
    # Assertive phrasings — these would tell the model to produce a file.
    bad_phrases = [
        "create a report document",
        "create a report",
        "claude code will browse",
        "create a report.html",
        "produce a report",
        "write a report",
    ]
    for phrase in bad_phrases:
        assert phrase not in lowered, (
            f"Research description still contains assertive instruction {phrase!r} — "
            "model will think it produces a file."
        )
    # And the prompt must include an explicit NEVER-style prohibition somewhere.
    assert ("never produce" in lowered
            or "never produces" in lowered
            or "no file" in lowered
            or "never a file" in lowered), (
        "Research description should explicitly tell the model NOT to produce a file."
    )
    print("✓ System prompt no longer tells the model research produces a file or delegates to Claude Code")


def test_system_prompt_describes_card_panel_output():
    research_section = _extract_research_section(JARVIS_SYSTEM_PROMPT).lower()
    assert "process panel" in research_section or "card" in research_section, (
        "Research description should tell the model output renders as cards in the Process Panel."
    )
    print("✓ System prompt frames research output as Process Panel cards")


def test_system_prompt_distinguishes_research_verbs_from_build_verbs():
    section = _extract_research_section(JARVIS_SYSTEM_PROMPT).lower()
    # Research verbs the rule must enumerate.
    for verb in ["show me", "find me", "what are", "tell me about", "research"]:
        assert verb in section, f"System prompt missing research verb example: {verb!r}"
    # Build verbs the rule must enumerate.
    for verb in ["build", "create a project", "new project"]:
        assert verb in section, f"System prompt missing build verb example: {verb!r}"
    print("✓ System prompt enumerates research and build verbs")


def test_system_prompt_handles_show_me_how_to_build_carveout():
    """Case #5: 'show me how to build X' must route to RESEARCH, not BUILD."""
    section = _extract_research_section(JARVIS_SYSTEM_PROMPT).lower()
    assert "how to build" in section or "show me how to" in section, (
        "System prompt must address the 'show me how to build X' carve-out — "
        "otherwise the model will keyword-match 'build' and dispatch a project."
    )
    print("✓ System prompt covers the 'show me how to build X' carve-out (case #5)")


def test_system_prompt_handles_ambiguous_create_a_project():
    """Case #6: 'create a project called X' should ask, not silently slugify."""
    section = _extract_research_section(JARVIS_SYSTEM_PROMPT).lower()
    assert "ask" in section or "ambiguous" in section, (
        "System prompt must instruct the model to ask when build vs research is ambiguous."
    )
    print("✓ System prompt instructs the model to ask when ambiguous (case #6)")


def test_design_optin_fast_path():
    """Chunk 20 — verify the 4 smoke routing cases from the user's spec
    against detect_action_fast directly (no LLM round-trip)."""
    from server import detect_action_fast

    cases = [
        # (utterance, expected_action_or_None, notes)
        # 1. Explicit opt-in via "talk about a feature" — design fires with no target.
        ("talk about a feature for jarvis-main",
         "start_design",
         "explicit opt-in phrase — empty target so Jarvis prompts for topic"),
        # 2. Direct build request — must NOT route to design.
        ("add a footer that says copyright 2026",
         None,  # falls through to LLM; not the fast-path's job to dispatch this
         "no design-intent words → LLM handles this as build/prompt_project"),
        # 3. Topic-capturing pattern still works (existing _START_DESIGN_PATTERN).
        ("let's design a new feature for RecipeBook Code",
         "start_design",
         "existing pattern captures topic, opt-in path is a no-op here"),
        # 4. The exact failure mode from the diagnosed session.
        ("yes please add the code",
         None,
         "user phrasing from chunk-19 diagnosis — must still route via LLM, "
         "NOT design fast-path"),
    ]

    for utterance, expected, why in cases:
        result = detect_action_fast(utterance, ws=None)
        actual = result["action"] if result else None
        assert actual == expected, (
            f"Routing mismatch for {utterance!r}:\n"
            f"  expected: {expected!r}\n"
            f"  got:      {actual!r}\n"
            f"  reason this matters: {why}"
        )

    # Bonus: the topic-capturing case must come back with the topic populated.
    r3 = detect_action_fast("let's design a new feature for RecipeBook Code", ws=None)
    assert r3 is not None and r3.get("target")
    assert "feature for RecipeBook Code" in r3["target"], r3

    # Bonus: the opt-in case must come back with EMPTY target (signals prompt-for-topic).
    r1 = detect_action_fast("talk about a feature for jarvis-main", ws=None)
    assert r1 is not None and r1.get("target") == "", r1

    print("✓ Design opt-in fast-path: 4 cases route correctly; targets populated as expected")


def test_chunk_20_bug_long_design_opt_in_routes_correctly():
    """Regression — the exact 13-word transcript from logs/jarvis.err.log
    at 17:02:22 (chunk-20 routing bug). Before chunk 21 this fell off the
    12-word cliff and reached the LLM instead of fast-pathing."""
    from server import detect_action_fast

    utterance = "let's talk about a feature I want to add to Jarvis Dash Main"
    assert len(utterance.split()) > 12, "test fixture invariant — must exceed the old cap"

    result = detect_action_fast(utterance, ws=None)
    assert result is not None, "13-word design opt-in must now reach the fast-path"
    assert result["action"] == "start_design", result
    assert result.get("target") == "", (
        f"Substring path should not capture a topic; got target={result.get('target')!r}"
    )
    print("✓ chunk-20 routing bug fixed — 13-word 'talk about a feature' utterance "
          "routes to start_design")


def test_dictation_routing_cases():
    """Mode 2 routing — the 5 explicit phrases all hit fast-path."""
    from server import detect_action_fast, _DICTATION_OPTIN_PHRASES

    cases = [
        "dictate to claude",
        "tell claude directly",
        "send claude a message",
        "dictation mode",
        "skip design",
        "dictate to claude — let's add a logout button",  # embedded variant
    ]
    for phrase in cases:
        result = detect_action_fast(phrase, ws=None)
        assert result is not None, f"{phrase!r} should fast-path"
        assert result["action"] == "start_dictation", (
            f"{phrase!r}: expected start_dictation, got {result!r}"
        )
    print(f"✓ All {len(_DICTATION_OPTIN_PHRASES)} dictation phrases route correctly")


def test_dictation_precedence_over_design():
    """Spec test 3 — an utterance containing BOTH a dictation phrase and
    a design phrase must route to dictation (the user explicitly chose
    the bypass)."""
    from server import detect_action_fast

    utterance = "dictate to claude — let's talk about a feature for jarvis-main"
    result = detect_action_fast(utterance, ws=None)
    assert result is not None
    assert result["action"] == "start_dictation", (
        f"Precedence violated: dictation should win over design, got {result!r}"
    )
    print("✓ Dictation precedence wins when both phrases present")


def test_no_regression_research_build_list():
    """Spec test 5 — common verbs must NOT accidentally route to dictation
    or design just because the new code lives above the word cap."""
    from server import detect_action_fast

    cases = [
        ("list my projects", "list_projects"),
        ("show me the three best fishing poles", None),  # research goes through LLM
        ("build me a recipe tracker", None),             # build goes through LLM
        ("add a footer that says copyright 2026", None), # build goes through LLM
    ]
    for utterance, expected_action in cases:
        result = detect_action_fast(utterance, ws=None)
        actual = result["action"] if result else None
        assert actual == expected_action, (
            f"{utterance!r}: expected action={expected_action!r}, got {actual!r}"
        )
        # Explicit check that none of these accidentally landed in mode 2 or design.
        assert actual not in ("start_dictation", "start_design"), (
            f"{utterance!r} should NOT route to a mode-entry action: {actual!r}"
        )
    print("✓ No regression — research/build/list utterances don't accidentally enter dictation/design")


def test_design_optin_phrases_all_match():
    """Every phrase in _DESIGN_OPTIN_PHRASES must trigger start_design with empty target."""
    from server import detect_action_fast, _DESIGN_OPTIN_PHRASES

    for phrase in _DESIGN_OPTIN_PHRASES:
        result = detect_action_fast(phrase, ws=None)
        # "let's design" alone matches the opt-in branch; "let's design X"
        # would match the existing topic-capturing pattern with a target.
        # Both are fine for this test: any start_design hit confirms the
        # phrase enters design mode somehow.
        assert result is not None and result["action"] == "start_design", (
            f"Opt-in phrase {phrase!r} failed to trigger start_design: got {result!r}"
        )
    print(f"✓ All {len(_DESIGN_OPTIN_PHRASES)} opt-in phrases trigger start_design")


def test_extract_urls_from_code_pulls_real_urls():
    """The chunk-17 fix relies on this parser to recover URLs from the
    Python source that _20260209 web_fetch hides them in. Test a few
    realistic patterns observed in /tmp/smoke_baseline.log."""
    from server import _extract_urls_from_code

    # Pattern 1: list literal + loop (the most common shape)
    code1 = (
        'import json\n'
        'urls = [\n'
        '    "https://www.outdoorgearlab.com/topics/camping-and-hiking/best-fishing-rod",\n'
        '    "https://www.wired2fish.com/fishing-videos/best-rod-and-tackle-setups-for-pond-fishing",\n'
        '    "https://fishingbooker.com/blog/beginner-fishing-rod/",\n'
        '    "https://www.wired2fish.com/crappie-fishing/choosing-the-right-panfish-rod"\n'
        ']\n'
        'for u in urls:\n'
        '    r = await web_fetch({"url": u})\n'
    )
    out1 = _extract_urls_from_code(code1)
    assert out1 == [
        "https://www.outdoorgearlab.com/topics/camping-and-hiking/best-fishing-rod",
        "https://www.wired2fish.com/fishing-videos/best-rod-and-tackle-setups-for-pond-fishing",
        "https://fishingbooker.com/blog/beginner-fishing-rod/",
        "https://www.wired2fish.com/crappie-fishing/choosing-the-right-panfish-rod",
    ], f"List-literal pattern wrong: {out1}"

    # Pattern 2: single literal in web_fetch call
    code2 = (
        'import json\n'
        'r = await web_fetch({"url": "https://fishingbooker.com/blog/beginner-fishing-rod/"})\n'
        'print(repr(r)[:500])\n'
    )
    out2 = _extract_urls_from_code(code2)
    assert out2 == ["https://fishingbooker.com/blog/beginner-fishing-rod/"], out2

    # Pattern 3: trailing punctuation stripped
    code3 = '''
print("checking", "https://example.com/foo,")
print("see https://example.com/bar.")
print("(https://example.com/baz)")
'''
    out3 = _extract_urls_from_code(code3)
    assert out3 == [
        "https://example.com/foo",
        "https://example.com/bar",
        "https://example.com/baz",
    ], out3

    # Pattern 4: no URLs — empty list, not an error
    assert _extract_urls_from_code("import json\nprint(42)") == []
    assert _extract_urls_from_code("") == []

    # Pattern 5: duplicates preserved (model may fetch same URL twice)
    code5 = '''
for u in ["https://a.com/x", "https://a.com/x"]:
    await web_fetch({"url": u})
'''
    out5 = _extract_urls_from_code(code5)
    assert out5 == ["https://a.com/x", "https://a.com/x"], out5

    print("✓ _extract_urls_from_code handles list literals, single calls, trailing punct, empties, dupes")


def test_middleware_strips_non_usd_prices():
    """Non-USD price strings should be replaced with None and logged."""
    from claude_middleware import _strip_non_usd_price

    cases = [
        ("$39.99",       ("$39.99",   False)),
        ("$1,299",       ("$1,299",   False)),
        ("USD 50",       ("USD 50",   False)),
        ("£40",          (None,       True)),     # pound symbol
        ("€42.50",       (None,       True)),     # euro symbol
        ("¥5000",        (None,       True)),     # yen symbol
        ("40 GBP",       (None,       True)),     # three-letter code
        ("50 eur",       (None,       True)),     # case-insensitive
        ("$50 (was £40)",(None,       True)),     # mixed → strip
        ("",             ("",         False)),    # empty
        (None,           (None,       False)),    # missing
    ]
    for price_in, (expected_clean, expected_stripped) in cases:
        clean, stripped = _strip_non_usd_price(price_in)
        assert clean == expected_clean, f"{price_in!r}: expected clean={expected_clean!r}, got {clean!r}"
        assert stripped == expected_stripped, f"{price_in!r}: expected stripped={expected_stripped}, got {stripped}"
    print("✓ Middleware strips non-USD prices (£/€/¥/three-letter codes/mixed)")


def test_middleware_enriches_product_cards_with_og_image():
    """Product cards with source_url but no image_url should be enriched
    via fetch_page_preview before emission. Patch the fetcher with a stub
    so the test doesn't hit the network."""
    import claude_middleware
    from claude_middleware import ResultCard, CardSet

    cards = [
        ResultCard(
            type="product",
            title="Ugly Stik GX2",
            summary="Solid budget rod.",
            source_url="https://example.com/ugly-stik",
            # image_url deliberately unset — middleware should fill it.
        ),
        ResultCard(
            type="product",
            title="Daiwa BG",
            summary="Mid-range option.",
            source_url="https://example.com/daiwa-bg",
            image_url="https://example.com/cached.jpg",  # already set — skip
        ),
        ResultCard(
            type="location",
            title="Bass Pro Ashland",
            summary="VA store.",
            source_url="https://example.com/store",  # not a product — skip
        ),
    ]

    fetch_calls: list[str] = []

    async def fake_fetch(url, timeout=1.5):
        fetch_calls.append(url)
        return {
            "url": url,
            "hostname": "example.com",
            "title": "Ugly Stik GX2 - Example",
            "og_image_url": "https://example.com/og.jpg",
        }

    import sys as _sys
    fake_module = type(_sys)("page_preview")
    fake_module.fetch_page_preview = fake_fetch
    original_module = _sys.modules.get("page_preview")
    _sys.modules["page_preview"] = fake_module
    try:
        asyncio.run(claude_middleware._enrich_card_images(cards))
    finally:
        if original_module is not None:
            _sys.modules["page_preview"] = original_module
        else:
            _sys.modules.pop("page_preview", None)

    # Only the first card (product, no image_url) should have been fetched.
    assert fetch_calls == ["https://example.com/ugly-stik"], (
        f"Expected exactly 1 fetch for the un-imaged product card, got: {fetch_calls}"
    )
    assert cards[0].image_url == "https://example.com/og.jpg", (
        f"First card's image_url should be enriched; got {cards[0].image_url!r}"
    )
    assert cards[1].image_url == "https://example.com/cached.jpg", (
        "Second card already had an image — should not be overwritten."
    )
    assert cards[2].image_url is None, "Location card should not be enriched."
    print("✓ Middleware enriches product cards (no overwrite of existing image_url)")


def test_dispatch_path_does_not_slugify_for_research():
    """Verify the dispatcher itself no longer calls _generate_project_name on research targets."""
    src = (Path(__file__).parent.parent / "server.py").read_text()
    # Find the research dispatch branch.
    m = re.search(
        r'elif embedded_action\["action"\] == "research":\s*\n((?:[ \t]+.*\n)+)',
        src,
    )
    assert m, "Could not locate the research dispatch branch in server.py"
    body = m.group(1)
    assert "_generate_project_name" not in body, (
        "Research dispatch still calls _generate_project_name — slugifying user queries into folder names."
    )
    assert "os.makedirs" not in body, (
        "Research dispatch still creates a directory — must be filesystem-readonly."
    )
    assert "work_session.start" not in body, (
        "Research dispatch still starts a WorkSession — must use native handler instead."
    )
    assert "_execute_native_research" in body, (
        "Research dispatch should call _execute_native_research."
    )
    print("✓ Research dispatch no longer slugifies, mkdir's, or spawns a WorkSession")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_research_section(prompt: str) -> str:
    """Return the [ACTION:RESEARCH] description block plus the disambiguation
    paragraphs that follow it, up to the next ACTION bullet."""
    lines = prompt.splitlines()
    out = []
    in_section = False
    for line in lines:
        if line.startswith("- [ACTION:RESEARCH]"):
            in_section = True
            out.append(line)
            continue
        if in_section:
            if line.startswith("- [ACTION:") and not line.startswith("- [ACTION:RESEARCH]"):
                break
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_extract_action_research,
    test_extract_action_distinguishes_research_from_build,
    test_native_research_does_not_touch_disk,
    test_native_research_uses_opus_47_and_web_tools,
    test_system_prompt_drops_report_document_language,
    test_system_prompt_describes_card_panel_output,
    test_system_prompt_distinguishes_research_verbs_from_build_verbs,
    test_system_prompt_handles_show_me_how_to_build_carveout,
    test_system_prompt_handles_ambiguous_create_a_project,
    test_design_optin_fast_path,
    test_chunk_20_bug_long_design_opt_in_routes_correctly,
    test_dictation_routing_cases,
    test_dictation_precedence_over_design,
    test_no_regression_research_build_list,
    test_design_optin_phrases_all_match,
    test_extract_urls_from_code_pulls_real_urls,
    test_middleware_strips_non_usd_prices,
    test_middleware_enriches_product_cards_with_og_image,
    test_dispatch_path_does_not_slugify_for_research,
]


def main():
    failed = 0
    for t in ALL_TESTS:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"✗ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"✗ {t.__name__}: unexpected error: {e!r}")
    print()
    if failed:
        print(f"FAIL — {failed} of {len(ALL_TESTS)} tests failed")
        sys.exit(1)
    print(f"PASS — all {len(ALL_TESTS)} tests passed")


if __name__ == "__main__":
    main()
