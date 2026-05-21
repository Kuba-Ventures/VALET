"""
Runtime self-introspection — what does JARVIS know about itself?

Scrapes runtime constants from source files (the WAKE_PREFIXES list in
wakeWord.ts, the agent registry from agents.py, …) so the LLM can answer
self-referential questions like "what are your wake phrases?" without
hallucinating. Called once per LLM request — the result is injected
into the dynamic system prompt (not cached) so refactors and additions
are reflected immediately, without restart.

Stays small on purpose: prompt tokens cost money on every turn. Each
section is one terse line. Add more facts here as future "JARVIS doesn't
know that about itself" gaps come up.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger("jarvis.self_knowledge")

JARVIS_ROOT = Path(__file__).resolve().parent
WAKE_WORD_FILE = JARVIS_ROOT / "frontend" / "src" / "wakeWord.ts"


# Regex pair to pull `const WAKE_PREFIXES = ["ok", "okay", "hey"] as const;`
# out of wakeWord.ts. Falls back to ["ok"] on any read/parse failure.
_WAKE_PREFIXES_RE = re.compile(r"WAKE_PREFIXES\s*=\s*\[(.*?)\]", re.DOTALL)
_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')


def read_wake_prefixes() -> list[str]:
    """Live-parse the WAKE_PREFIXES array from wakeWord.ts."""
    try:
        text = WAKE_WORD_FILE.read_text()
    except Exception as e:
        log.debug(f"read_wake_prefixes: read failed: {e}")
        return ["ok"]
    m = _WAKE_PREFIXES_RE.search(text)
    if not m:
        return ["ok"]
    prefixes: list[str] = []
    for double, single in _QUOTED_RE.findall(m.group(1)):
        prefixes.append(double or single)
    return prefixes or ["ok"]


def assistant_name() -> str:
    """Pull the configured assistant name from env, same source the
    frontend reads via /api/config. Lower-cased to match the wake regex."""
    return (os.getenv("ASSISTANT_NAME", "") or "jarvis").lower()


def wake_phrases(name: str | None = None) -> list[str]:
    """Compose full "<prefix> <name>" phrases the wake regex actually
    accepts. Mirrors buildWakeRegex in wakeWord.ts."""
    n = (name or assistant_name())
    return [f"{p} {n}" for p in read_wake_prefixes()]


def get_self_knowledge_block(name: str | None = None) -> str:
    """One small prompt-ready block answering 'what do I know about
    myself right now?'. Goes in dynamic_system (uncached) so the LLM
    sees current state every turn.

    Keep lines terse — every byte costs tokens on every voice turn.
    """
    n = (name or assistant_name())
    lines = ["ABOUT YOURSELF (live runtime state):"]

    # Wake phrases — the user's #1 self-introspection question so far.
    phrases = wake_phrases(n)
    lines.append(
        f"- Wake phrases (any of these activates you): "
        + " / ".join(f'"{p}"' for p in phrases)
    )

    # Sub-agents — the new dispatch surface (chunk 27).
    try:
        import agents  # local import; agents.py is sibling
        agent_names = [a["name"] for a in agents.list_agents(JARVIS_ROOT)]
        if agent_names:
            lines.append(
                f"- Sub-agents you can dispatch ship-it work to: {', '.join(agent_names)}"
            )
            lines.append(
                "- Voice dispatch: \"use the <agent> agent to <task>\" routes directly without going through design partner."
            )
    except Exception as e:
        log.debug(f"get_self_knowledge_block: agents lookup failed: {e}")

    return "\n".join(lines)
