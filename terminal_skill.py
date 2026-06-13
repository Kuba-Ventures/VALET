"""Voice terminal + Cursor skills (Phase K — UC6).

Net-new skills Jacques named that didn't exist yet:

  * run_command — run a shell command by voice, **safety-tiered**: read-only
    commands (git status, ls, pwd, …) run automatically (Tier 0); everything
    else confirms (Tier 1), with an extra warning on clearly destructive ones.
  * cursor_goto — open a file at a line in Cursor (cursor:// URL).
  * cursor_symbol — search a symbol in Cursor, driven through the UC1/UC3
    universal layer (open Cursor → ⇧⌘O → type) at the call site.

This module is pure classification + execution; the safety gate (confirm card +
kill switch) and the voice wiring live in server.py, which calls `classify_*`
and `run_command` here.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Optional

# Voice → shell fixups: browser speech-to-text mangles dev terms ("git"→"get"/
# "github", "rm -rf"→"rm space — rf", "grep"→"grip"). Applied to a spoken command
# before classify/execute; the confirm card still shows the final command, so an
# imperfect fixup stays visible and vetoable.
_VOICE_FIXUPS = [
    (re.compile(r"^\s*(?:the\s+command\s+)?", re.I), ""),     # drop "the command "
    (re.compile(r"[—–]"), "-"),                                # em/en dash → hyphen
    (re.compile(r"^\s*(?:get|github)\b", re.I), "git"),        # get/github <sub> → git
    (re.compile(r"\bgr[ia][bp]\b", re.I), "grep"),             # grip/grab/grap → grep
    (re.compile(r"\bpseudo\b", re.I), "sudo"),
    (re.compile(r"\bpiped?\s+to\b", re.I), "|"),               # "piped to grep" → "| grep"
    (re.compile(r"\s+space\s+(?=-)", re.I), " "),              # "rm space -rf" → "rm -rf"
    (re.compile(r"\bdash\s+(?=[a-z])", re.I), "-"),            # "dash rf" → "-rf"
]


def normalize_command(cmd: str) -> str:
    """Best-effort fixups for a voice-dictated shell command."""
    c = cmd or ""
    for pat, rep in _VOICE_FIXUPS:
        c = pat.sub(rep, c)
    c = re.sub(r"\s{2,}", " ", c).strip()
    # STT often capitalizes the leading word ("RM", "LS"); the program name is
    # lowercase by convention. Lowercase only the first token, keep args' case.
    head, _, rest = c.partition(" ")
    return f"{head.lower()} {rest}".strip() if head else c


# First tokens that are read-only (Tier 0). Conservative on purpose — anything
# not clearly read-only falls through to Tier 1 (confirm).
_READONLY = {
    "ls", "pwd", "cat", "head", "tail", "less", "wc", "which", "whoami", "id",
    "date", "df", "du", "uname", "hostname", "uptime", "env", "printenv",
    "echo", "tree", "file", "stat", "ps", "top", "history", "type",
}
# git subcommands that don't mutate the repo.
_READONLY_GIT = {
    "status", "log", "diff", "show", "branch", "remote", "describe", "blame",
    "shortlog", "rev-parse", "ls-files", "tag", "config", "reflog", "whatchanged",
}
# package-manager invocations that are read-only (version / list / help).
_READONLY_PM = {"node", "npm", "python", "python3", "pip", "pip3", "go", "cargo",
                "yarn", "pnpm", "bun", "deno", "ruby", "rustc", "java"}
_READONLY_PM_ARGS = {"-v", "--version", "version", "ls", "list", "--help", "-h", "outdated"}

# Shell metacharacters: if present we can't safely classify → always confirm.
_META = (";", "|", "&", ">", "<", "`", "$(", "{", "}", "\n")

# Clearly destructive patterns → confirm AND warn loudly.
_DANGER = (
    "rm -rf", "rm -fr", "sudo ", "mkfs", "dd ", ":(){", "shutdown", "reboot",
    "git push --force", "git push -f", "git reset --hard", "git clean -fd",
    "kubectl delete", "drop table", "drop database", "> /dev", "chmod -r 777",
    "killall", "diskutil erase",
)


def classify_command(cmd: str) -> int:
    """0 (Tier 0, auto) for clearly read-only commands; 1 (Tier 1, confirm) else."""
    c = (cmd or "").strip()
    if not c or any(m in c for m in _META):
        return 1
    try:
        toks = shlex.split(c)
    except ValueError:
        return 1
    if not toks:
        return 1
    head = toks[0]
    if head in _READONLY:
        return 0
    if head == "git" and len(toks) >= 2 and toks[1] in _READONLY_GIT:
        # git branch -d / config --set (with a value) mutate.
        if toks[1] == "branch" and any(t in ("-d", "-D", "-m", "-M", "--delete") for t in toks):
            return 1
        if toks[1] == "config" and not any(t in ("-l", "--list", "--get") for t in toks) and len(toks) > 3:
            return 1  # `git config key value` writes
        return 0
    if head in _READONLY_PM and any(a in _READONLY_PM_ARGS for a in toks[1:]):
        return 0
    return 1


def danger_warning(cmd: str) -> Optional[str]:
    """A loud warning string for clearly destructive commands, else None."""
    low = (cmd or "").lower()
    if any(d in low for d in _DANGER):
        return "This looks destructive — it can delete or overwrite things permanently."
    return None


async def run_command(cmd: str, cwd: Optional[str] = None, timeout: float = 30.0) -> dict:
    """Run `cmd` in a shell and capture output. Returns {ok, code, stdout, stderr}.

    Gating is the caller's job — by the time this runs, a Tier-1 command has
    already been confirmed."""
    workdir = str(Path(cwd).expanduser()) if cwd else str(Path.home())
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=workdir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except Exception as e:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(e)[:500]}
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"timed out after {timeout:.0f}s"}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode or 0,
        "stdout": out.decode(errors="replace")[:4000],
        "stderr": err.decode(errors="replace")[:2000],
    }


def summarize_result(cmd: str, result: dict) -> str:
    """A short spoken line for a command result."""
    if result.get("ok"):
        out = (result.get("stdout") or "").strip()
        first = out.splitlines()[0] if out else ""
        if not out:
            return "Done, sir."
        return f"Done, sir. {first[:120]}" if first else "Done, sir."
    err = (result.get("stderr") or "").strip().splitlines()
    return f"That failed, sir: {err[0][:120]}" if err else "That command failed, sir."


# --------------------------------------------------------------------------- #
# Cursor
# --------------------------------------------------------------------------- #
def cursor_goto_url(path: str, line: int = 1) -> str:
    """A cursor:// deep link to open `path` at `line` (Cursor = VS Code-based)."""
    abs_path = str(Path(path).expanduser().resolve())
    line = max(1, int(line or 1))
    return f"cursor://file{abs_path}:{line}"
