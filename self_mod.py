"""
JARVIS self-modification machinery (Phase 5).

The design partner can target the JARVIS repo itself. When that happens the
ship-it flow takes an additional path through this module:

  1. Approval gate    — JARVIS speaks "I'm about to modify myself. Confirm?"
                        and refuses to ship without the explicit phrase
                        "confirmed". Implemented in server._execute_ship_design
                        + _handle_pending_offer via kind="self_mod_confirm".
  2. Branch discipline — assert_clean_tree() refuses on dirty WT;
                        create_feature_branch() makes feature/<slug>.
  3. Smoke gate        — run_smoke_test() runs scripts/smoke_test.sh; on fail
                        the user gets the failure speech + the branch sits
                        on the feature branch unmerged (JARVIS never auto-
                        resets per the rules — user decides).
  4. Merge             — merge_to_main(branch) does
                        `git checkout main && git merge --no-ff <branch>`,
                        only invoked from the explicit "merge it" voice
                        command, never automatically.
  5. Restart           — restart_self() shells out to scripts/restart.sh
                        which spawns a detached restarter.

ALL OF THIS IS INERT TONIGHT. The first end-to-end self-mod ship happens
with the user watching in the morning.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.self_mod")

JARVIS_REPO = Path(__file__).resolve().parent
SMOKE_SCRIPT = JARVIS_REPO / "scripts" / "smoke_test.sh"
RESTART_SCRIPT = JARVIS_REPO / "scripts" / "restart.sh"


# ---------------------------------------------------------------------------
# Repo identity
# ---------------------------------------------------------------------------

def is_jarvis_repo(path: Optional[Path]) -> bool:
    """True if `path` is (a) Path-resolved equal to JARVIS_REPO."""
    if path is None:
        return False
    try:
        return Path(path).resolve() == JARVIS_REPO
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Git operations — all run with cwd=JARVIS_REPO, no `--no-verify` shortcuts.
# ---------------------------------------------------------------------------

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(JARVIS_REPO),
        capture_output=True,
        text=True,
        check=check,
    )


def current_branch() -> str:
    return _git("branch", "--show-current").stdout.strip()


def current_sha() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def working_tree_dirty() -> bool:
    """True if git status --porcelain returns anything (modified, untracked, staged)."""
    out = _git("status", "--porcelain", "--ignore-submodules=none").stdout
    return bool(out.strip())


def assert_clean_tree() -> None:
    """Raise RuntimeError with the offending paths if the tree is dirty."""
    if working_tree_dirty():
        out = _git("status", "--porcelain", "--ignore-submodules=none").stdout
        raise RuntimeError(f"Working tree dirty — won't self-mod:\n{out.rstrip()}")


def _slugify(text: str) -> str:
    """Kebab-case, alphanumeric+hyphen only, capped at 40 chars."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "untitled"


def create_feature_branch(topic: str) -> tuple[str, str]:
    """`git checkout -b feature/<slug>` and return (branch_name, pre_build_sha).

    Refuses if not currently on main or another feature/* branch (no
    detached HEAD, no rogue branches). Refuses if WT dirty.
    """
    assert_clean_tree()
    cur = current_branch()
    if not (cur == "main" or cur.startswith("feature/") or cur.startswith("overnight/")):
        raise RuntimeError(f"Refusing to branch from {cur!r} — must be on main / feature/* / overnight/*.")

    pre_sha = current_sha()
    slug = _slugify(topic)
    branch = f"feature/{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
    _git("checkout", "-b", branch)
    log.info(f"create_feature_branch: created {branch} at {pre_sha[:8]}")
    return branch, pre_sha


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

async def run_smoke_test(timeout_sec: int = 120) -> dict:
    """Run scripts/smoke_test.sh, capture output, return {success, returncode, stdout, stderr}.

    Never raises — always returns a dict the caller can speak from.
    """
    if not SMOKE_SCRIPT.exists():
        return {"success": False, "returncode": -1, "stdout": "", "stderr": f"missing {SMOKE_SCRIPT}"}

    try:
        proc = await asyncio.create_subprocess_exec(
            str(SMOKE_SCRIPT),
            cwd=str(JARVIS_REPO),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            try: proc.kill()
            except ProcessLookupError: pass
            return {"success": False, "returncode": -2, "stdout": "", "stderr": f"smoke test exceeded {timeout_sec}s"}
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout_b.decode(errors="replace"),
            "stderr": stderr_b.decode(errors="replace"),
        }
    except Exception as e:
        return {"success": False, "returncode": -3, "stdout": "", "stderr": f"smoke spawn failed: {e}"}


# ---------------------------------------------------------------------------
# Merge / reset
# ---------------------------------------------------------------------------

def merge_to_main(branch: str) -> dict:
    """`git checkout main && git merge --no-ff <branch>`. Returns {success, message}.

    Refuses if branch isn't a feature/*. Never force-pushes, never deletes
    branches — user does cleanup manually.
    """
    if not branch.startswith("feature/"):
        return {"success": False, "message": f"Refusing to merge {branch} — only feature/* branches."}
    if working_tree_dirty():
        return {"success": False, "message": "Working tree dirty — commit or stash before merging."}
    try:
        _git("checkout", "main")
        result = _git("merge", "--no-ff", branch, check=False)
        if result.returncode != 0:
            return {"success": False, "message": f"Merge failed:\n{result.stderr[:400]}"}
        return {"success": True, "message": f"Merged {branch} into main."}
    except subprocess.CalledProcessError as e:
        return {"success": False, "message": f"git failed: {e.stderr[:400] if e.stderr else e}"}


def reset_to(sha: str) -> dict:
    """`git reset --hard <sha>`. Caller-protected: only call this when the
    user explicitly asked OR after a confirmed smoke fail. Never auto-called
    from a passive code path. Returns {success, message}."""
    if not re.match(r"^[0-9a-f]{7,40}$", sha):
        return {"success": False, "message": f"refusing to reset to {sha!r} — invalid sha"}
    try:
        _git("reset", "--hard", sha)
        return {"success": True, "message": f"Reset HEAD to {sha[:8]}."}
    except subprocess.CalledProcessError as e:
        return {"success": False, "message": f"reset failed: {e.stderr[:400] if e.stderr else e}"}


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

def restart_self() -> dict:
    """Spawn scripts/restart.sh detached. Returns {success, message}.

    Caller (JARVIS Python process) should speak the confirmation BEFORE this
    returns and not assume anything works after — the restarter will kill
    this process shortly.
    """
    if not RESTART_SCRIPT.exists():
        return {"success": False, "message": f"missing {RESTART_SCRIPT}"}
    try:
        # Fully detach — Popen with new session so the restarter survives
        # the act of killing the parent.
        subprocess.Popen(
            [str(RESTART_SCRIPT)],
            cwd=str(JARVIS_REPO),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"success": True, "message": "Restarter spawned. Goodnight for ~5s, sir."}
    except Exception as e:
        return {"success": False, "message": f"restart spawn failed: {e}"}
