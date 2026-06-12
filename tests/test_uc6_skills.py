"""UC6 — skills (terminal + Cursor) + registry: headless unit tests.

Covers the command safety tiering (the security-critical part), the free/paid
registry seam, the Cursor deep-link builder, and the voice fast-path regexes.
No GUI/API key; nothing is executed.

Run:  ./.venv/bin/python -m pytest tests/test_uc6_skills.py -q
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import skills
import terminal_skill as ts


# --------------------------------------------------------------------------- #
# Command safety tiering (Tier 0 = auto, Tier 1 = confirm)
# --------------------------------------------------------------------------- #
def test_readonly_commands_are_tier0():
    for c in ["git status", "git log --oneline", "git diff", "ls -la", "pwd",
              "whoami", "df -h", "npm --version", "node -v", "git branch"]:
        assert ts.classify_command(c) == 0, c


def test_mutating_commands_are_tier1():
    for c in ["npm install", "git commit -m x", "git push", "rm file.txt",
              "mkdir foo", "git branch -d topic", "brew install jq", "make build",
              "git config user.name Bob"]:
        assert ts.classify_command(c) == 1, c


def test_shell_metacharacters_force_confirm():
    for c in ["git status | grep foo", "ls > out.txt", "echo hi && rm x",
              "cat $(whoami)", "ls; rm -rf ."]:
        assert ts.classify_command(c) == 1, c


def test_danger_warning():
    assert ts.danger_warning("rm -rf /") is not None
    assert ts.danger_warning("sudo rm x") is not None
    assert ts.danger_warning("git push --force") is not None
    assert ts.danger_warning("git status") is None


# --------------------------------------------------------------------------- #
# Skill registry — the free/paid seam
# --------------------------------------------------------------------------- #
def test_registry_tiers():
    assert skills.get("open_app").tier is skills.Tier.FREE
    assert skills.get("run_command").tier is skills.Tier.PAID
    assert skills.is_paid("draft_email") is True
    assert skills.is_paid("open_app") is False
    assert skills.get("does_not_exist") is None


def test_gate_reports_boundary_without_enforcing():
    g_free = skills.gate("run_command", plan="free")
    assert g_free["allowed"] is True and g_free["would_gate"] is True   # paid skill, free plan
    g_paid = skills.gate("run_command", plan="pro")
    assert g_paid["allowed"] is True and g_paid["would_gate"] is False
    g_free_skill = skills.gate("open_app", plan="free")
    assert g_free_skill["would_gate"] is False
    assert skills.gate("unknown")["known"] is False


# --------------------------------------------------------------------------- #
# Cursor deep link
# --------------------------------------------------------------------------- #
def test_cursor_goto_url():
    url = ts.cursor_goto_url("~/Code/VALET/server.py", 42)
    assert url.startswith("cursor://file/") and url.endswith(":42")
    assert ts.cursor_goto_url("/tmp/x.py", 0).endswith(":1")  # clamps to >=1


# --------------------------------------------------------------------------- #
# Voice fast-path regexes (mirror the server patterns)
# --------------------------------------------------------------------------- #
_RUN = re.compile(
    r'^(?:run|execute)\s+(?:the\s+command\s+)?'
    r'(?P<cmd>(?:git|npm|node|python3?|pip3?|ls|pwd|cargo|go|yarn|pnpm|brew|make|'
    r'docker|kubectl|cat|grep|find|mkdir|touch|cp|mv|rm|echo|curl|ssh|ps|df|du)\b.*?)'
    r'(?:\s+in\s+(?:the\s+)?terminal)?\s*[.?]?$', re.IGNORECASE)
_GOTO = re.compile(r'^open\s+(?P<file>.+?)\s+(?:at\s+|on\s+)?line\s+(?P<line>\d+)\s+in\s+cursor\s*[.?]?$', re.IGNORECASE)
_SYM = re.compile(r'^(?:search|find|go\s*to|jump\s*to)\s+(?:the\s+)?symbol\s+(?P<sym>.+?)\s*(?:in\s+cursor)?\s*[.?]?$', re.IGNORECASE)


def test_run_regex_matches_known_tools_only():
    assert _RUN.match("run npm install").group("cmd") == "npm install"
    assert _RUN.match("run git status").group("cmd") == "git status"
    assert _RUN.match("execute the command ls -la in terminal").group("cmd") == "ls -la"
    assert _RUN.match("run the tests") is None       # not a known tool → conversation
    assert _RUN.match("run a marathon") is None


def test_cursor_regexes():
    g = _GOTO.match("open server.py at line 42 in cursor")
    assert g.group("file") == "server.py" and g.group("line") == "42"
    s = _SYM.match("search symbol build_observation in cursor")
    assert s.group("sym") == "build_observation"
    assert _SYM.match("go to symbol run_loop").group("sym") == "run_loop"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
