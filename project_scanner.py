"""Project scanning — discovers git repos under known roots and the alias
table, and formats them for the system prompt. Extracted from server.py.
"""

import logging
from pathlib import Path

from actions import list_projects

log = logging.getLogger("valet.projects")


# Project Scanner
# ---------------------------------------------------------------------------

async def scan_projects() -> list[dict]:
    """Scan known project roots (~/Code, ~/projects) + the alias table for git repos.

    Delegates discovery to `list_projects()` so any new root the lifecycle
    layer learns about is automatically reflected here. Only surfaces git-
    tracked dirs to keep the LLM context useful.
    """
    projects = []
    for entry in list_projects():
        path = Path(entry["path"])
        if not path.is_dir():
            continue
        git_dir = path / ".git"
        if not git_dir.exists():
            continue
        branch = "unknown"
        head_file = git_dir / "HEAD"
        try:
            head_content = head_file.read_text().strip()
            if head_content.startswith("ref: refs/heads/"):
                branch = head_content.replace("ref: refs/heads/", "")
        except Exception:
            pass
        projects.append({
            "name": entry["name"],
            "path": str(path),
            "branch": branch,
        })
    return projects


def format_projects_for_prompt(projects: list[dict]) -> str:
    if not projects:
        return "No projects on record."
    lines = []
    for p in projects:
        lines.append(f"- {p['name']} ({p['branch']}) @ {p['path']}")
    return "\n".join(lines)
