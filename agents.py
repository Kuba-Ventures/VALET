"""
Claude Code sub-agent discovery.

Enumerates the agents available to a running Claude Code session so VALET
can let the user pick which one to dispatch ship-it work to (via the design
panel's agent dropdown, an auto-detect heuristic, or a direct voice command).

Sources, in priority order:
  1. <project>/.claude/agents/*.md    project-local custom agents
  2. ~/.claude/agents/*.md            user-global custom agents
  3. BUILTIN_AGENTS list below        Claude Code's stock agents

Each .md file has YAML frontmatter with `name` and `description`. We do a
forgiving parse (regex on the first frontmatter block) rather than pulling
in PyYAML — the format is stable enough that two lines of regex suffice.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("valet.agents")


# Stock agents available to every Claude Code session. Keep names matching
# Claude Code's Agent-tool subagent_type vocabulary so paste-time prompt
# headers refer to them correctly.
BUILTIN_AGENTS = [
    {
        "name": "general-purpose",
        "description": "Catch-all for multi-step research, exploration, and execution tasks.",
        "source": "builtin",
    },
    {
        "name": "Plan",
        "description": "Software architect; returns implementation plans, identifies critical files, weighs trade-offs.",
        "source": "builtin",
    },
    {
        "name": "Explore",
        "description": "Fast read-only search agent for locating code by pattern, symbol, or keyword.",
        "source": "builtin",
    },
    {
        "name": "claude-code-guide",
        "description": "Answers questions about Claude Code itself, the Agent SDK, and the Claude API.",
        "source": "builtin",
    },
]


_NAME_RE = re.compile(r'^\s*name:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_DESC_RE = re.compile(r'^\s*description:\s*"?(.+?)"?\s*$', re.MULTILINE | re.DOTALL)


def _parse_agent_file(path: Path) -> Optional[dict]:
    """Extract {name, description} from an agent .md file's frontmatter.

    Returns None if the file doesn't look like a valid agent definition.
    Description is capped at 160 chars so the frontend dropdown stays sane.
    """
    try:
        text = path.read_text()
    except Exception as e:
        log.debug(f"_parse_agent_file: read failed for {path}: {e}")
        return None
    if not text.startswith("---"):
        return None
    # Slice just the frontmatter block so we don't accidentally match body text.
    end = text.find("---", 3)
    if end < 0:
        return None
    block = text[3:end]
    name_m = _NAME_RE.search(block)
    if not name_m:
        return None
    name = name_m.group(1).strip()
    desc_m = _DESC_RE.search(block)
    desc = desc_m.group(1).strip() if desc_m else ""
    # First sentence of the description is enough for a dropdown row.
    desc = re.split(r"(?<=\.)\s", desc, maxsplit=1)[0]
    if len(desc) > 160:
        desc = desc[:157] + "…"
    return {"name": name, "description": desc}


def _scan_dir(dir_path: Path, source_label: str) -> list[dict]:
    out: list[dict] = []
    if not dir_path.is_dir():
        return out
    for entry in sorted(dir_path.glob("*.md")):
        parsed = _parse_agent_file(entry)
        if parsed:
            parsed["source"] = source_label
            out.append(parsed)
    return out


def list_agents(project_path: Optional[Path] = None) -> list[dict]:
    """Return every agent VALET knows about, de-duped by name.

    Project-local entries win over user-global, which win over builtins.
    Shape: [{"name": str, "description": str, "source": "project"|"user"|"builtin"}].
    """
    seen: dict[str, dict] = {}

    if project_path:
        for a in _scan_dir(project_path / ".claude" / "agents", "project"):
            seen[a["name"]] = a

    home_agents = Path.home() / ".claude" / "agents"
    for a in _scan_dir(home_agents, "user"):
        if a["name"] not in seen:
            seen[a["name"]] = a

    for a in BUILTIN_AGENTS:
        if a["name"] not in seen:
            seen[a["name"]] = dict(a)

    return list(seen.values())


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

# Keyword → agent mapping for the "auto" detection heuristic. First match wins;
# order matters when a draft contains multiple cues. Keywords are tested as
# substrings against the lowercased concatenation of all draft fields.
_AGENT_KEYWORDS: list[tuple[str, str]] = [
    ("security review", "security-review"),
    ("vulnerability",   "security-review"),
    ("security audit",  "security-review"),
    ("debug",           "debugger"),
    ("stack trace",     "debugger"),
    ("error",           "debugger"),
    ("test fail",       "debugger"),
    ("documentation",   "docs-right"),
    ("sensitive data",  "docs-right"),
    ("brand",           "style-steward"),
    ("voice",           "style-steward"),
    ("copy",            "style-steward"),
    ("ui copy",         "style-steward"),
    ("review code",     "code-review"),
    ("code review",     "code-review"),
    ("plan",            "Plan"),
    ("design",          "Plan"),
    ("architecture",    "Plan"),
    ("search for",      "Explore"),
    ("find where",      "Explore"),
    ("locate",          "Explore"),
    ("grep",            "Explore"),
]


def auto_detect_agent(draft_text: str, available: list[dict]) -> Optional[str]:
    """Heuristic pick from the draft. Returns an agent name or None.

    `available` is the list_agents() result — used to gate suggestions to
    agents that actually exist (no point telling Claude to use an agent it
    can't dispatch). Falls back to None when nothing matches.
    """
    available_names = {a["name"].lower() for a in available}
    haystack = draft_text.lower()
    for keyword, agent in _AGENT_KEYWORDS:
        if keyword in haystack and agent.lower() in available_names:
            return agent
    return None


def format_dispatch_header(agent: str) -> str:
    """The prompt prefix paste_into_cursor_claude prepends when an agent is set.

    Centralized so the wording stays consistent across the design ship path,
    the self-mod ship path, and the "use the X agent" voice fast-path.
    """
    return (
        f"Dispatch this work to the `{agent}` sub-agent via the Agent tool. "
        f"Use subagent_type=\"{agent}\" and pass through the brief below.\n\n"
    )
