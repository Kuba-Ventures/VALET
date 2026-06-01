"""
JARVIS warm-context loader.

When a project is opened (via new_cursor_project / open_project), JARVIS reads
a light slice of context — file tree, CLAUDE.md, README, recent git log, and
1–3 entry points — into memory so the design-partner conversation in Phase 3
can be grounded without exhaustive code-indexing.

Contexts are keyed by absolute project path. A `watchdog.Observer` runs per
project, debouncing fs events at `warm_context.fs_debounce_ms` (default 500ms)
before refreshing.

All knobs (depth, line caps, debounce) live in `config/design_partner.json`
under the `warm_context` key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from process_events import bus, emit_context_event

log = logging.getLogger("jarvis.project_context")

# Loaded contexts, keyed by absolute project path.
_contexts: dict[Path, "ProjectContext"] = {}

# Per-project watcher state: {path: {"observer": Observer, "handler": Handler}}
_watchers: dict[Path, dict] = {}

# Most-recently-loaded project — used by "refresh context" voice command when
# the user doesn't name a specific project.
_active_path: Path | None = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config" / "design_partner.json"
_cached_warm_cfg: dict | None = None


def _warm_config() -> dict:
    """Load `warm_context` block from config/design_partner.json. Defaults baked in."""
    global _cached_warm_cfg
    if _cached_warm_cfg is not None:
        return _cached_warm_cfg
    defaults = {
        "file_tree_depth": 3,
        "git_log_count": 20,
        "entry_point_lines": 200,
        "max_entry_points": 3,
        "fs_debounce_ms": 500,
        "max_context_chars": 6000,
    }
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text())
            defaults.update(data.get("warm_context", {}))
    except Exception as e:
        log.warning(f"design_partner.json unreadable: {e}")
    _cached_warm_cfg = defaults
    return _cached_warm_cfg


# ---------------------------------------------------------------------------
# ProjectContext
# ---------------------------------------------------------------------------

@dataclass
class ProjectContext:
    project_path: Path
    file_tree: str = ""
    claude_md: str | None = None
    readme: str | None = None
    git_log: str = ""
    entry_points: dict[str, str] = field(default_factory=dict)
    loaded_at: float = 0.0

    def summary_for_prompt(self) -> str:
        """Render as a markdown block for LLM injection (Phase 4 ship-it)."""
        cfg = _warm_config()
        parts = [f"# Project: {self.project_path.name}", f"Path: {self.project_path}"]
        if self.claude_md:
            parts.append("## CLAUDE.md\n" + self.claude_md.strip())
        if self.readme:
            parts.append(f"## README.md (first {cfg['entry_point_lines']} lines)\n" + self.readme.strip())
        if self.git_log:
            parts.append("## Recent commits\n" + self.git_log.strip())
        if self.file_tree:
            parts.append(f"## File tree (depth {cfg['file_tree_depth']})\n```\n{self.file_tree.strip()}\n```")
        for name, body in self.entry_points.items():
            parts.append(f"## Entry point: {name} (first {cfg['entry_point_lines']} lines)\n```\n{body.strip()}\n```")
        rendered = "\n\n".join(parts)
        # Hard cap so the composed ship prompt stays inside what the auto-paste
        # path can reliably deliver into Cursor's claude terminal. Large repos
        # (full CLAUDE.md + README + 3×200-line entry points) can otherwise
        # render tens of KB; a clipboard paste that big races the Enter key and
        # silently fails to land. Truncate with a visible marker.
        max_chars = cfg.get("max_context_chars", 6000)
        if max_chars and len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip() + "\n\n…[warm context truncated]"
        return rendered


# ---------------------------------------------------------------------------
# Reader helpers
# ---------------------------------------------------------------------------

async def _read_file_tree(project_path: Path, depth: int) -> str:
    """Gitignore-aware tree via `git ls-files`; fallback to depth-limited walk."""
    if (project_path / ".git").exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "ls-files",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                files = stdout.decode().splitlines()
                filtered = [f for f in files if f.count("/") < depth]
                return "\n".join(sorted(filtered))
        except Exception as e:
            log.warning(f"git ls-files failed in {project_path}: {e}")

    # Fallback: depth-limited walk, skip hidden + heavy dirs
    skip_dirs = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    root_depth = len(project_path.parts)
    lines: list[str] = []
    for cur, dirs, files in os.walk(project_path):
        cur_path = Path(cur)
        cur_depth = len(cur_path.parts) - root_depth
        if cur_depth >= depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip_dirs]
        rel = cur_path.relative_to(project_path)
        for f in sorted(files):
            if f.startswith("."):
                continue
            lines.append(str(rel / f) if str(rel) != "." else f)
    return "\n".join(sorted(lines))


async def _read_git_log(project_path: Path, count: int) -> str:
    if not (project_path / ".git").exists():
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", f"--max-count={count}", "--oneline",
            cwd=str(project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception as e:
        log.warning(f"git log failed in {project_path}: {e}")
    return ""


def _detect_entry_points(project_path: Path, claude_md: str | None, max_count: int) -> list[Path]:
    """Heuristic: Python conventional names, Node `main` field, CLAUDE.md backticked paths."""
    candidates: list[Path] = []

    for name in ("server.py", "main.py", "app.py", "__main__.py"):
        p = project_path / name
        if p.exists():
            candidates.append(p)

    pkg = project_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            main = data.get("main", "")
            if main:
                p = project_path / main
                if p.exists():
                    candidates.append(p)
        except Exception:
            pass

    if claude_md:
        for match in re.findall(r"`([^`\n]+\.(?:py|ts|tsx|js|jsx|go|rs|rb))`", claude_md):
            p = project_path / match
            if p.exists():
                candidates.append(p)

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique[:max_count]


def _read_lines(path: Path, n: int) -> str:
    try:
        with path.open("r", errors="replace") as f:
            return "".join(f.readline() for _ in range(n))
    except Exception as e:
        log.warning(f"_read_lines({path}) failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load(project_path: Path, reason: str = "initial") -> ProjectContext:
    """Load (or reload) warm context for a project. Idempotent.

    On the first call for a path, starts a watchdog observer. Subsequent calls
    just re-read. `reason='refresh'` causes the final panel event to be
    `context.refreshed` instead of `context.ready`.
    """
    global _active_path
    project_path = Path(project_path).resolve()
    _active_path = project_path

    cfg = _warm_config()
    final_stage = "refreshed" if reason == "refresh" else "ready"

    async with bus.task_context(
        f"{'Refreshing' if reason == 'refresh' else 'Loading'} context: {project_path.name}",
        detail=str(project_path),
    ) as task_id:
        await emit_context_event(
            task_id, "loading", "Reading project context",
            detail=str(project_path), status="active",
        )

        ctx = ProjectContext(project_path=project_path)

        # CLAUDE.md
        claude_md_path = project_path / "CLAUDE.md"
        if claude_md_path.exists():
            try:
                ctx.claude_md = claude_md_path.read_text()
                await emit_context_event(
                    task_id, "file_read", "CLAUDE.md",
                    detail=f"{len(ctx.claude_md)} chars", status="done",
                )
            except Exception as e:
                log.warning(f"Read CLAUDE.md failed: {e}")

        # README.md (first N lines)
        readme_path = project_path / "README.md"
        if readme_path.exists():
            ctx.readme = _read_lines(readme_path, cfg["entry_point_lines"])
            await emit_context_event(task_id, "file_read", "README.md", status="done")

        # File tree
        ctx.file_tree = await _read_file_tree(project_path, cfg["file_tree_depth"])
        if ctx.file_tree:
            line_count = ctx.file_tree.count("\n") + 1
            await emit_context_event(
                task_id, "file_read", "File tree",
                detail=f"{line_count} entries", status="done",
            )

        # Git log
        ctx.git_log = await _read_git_log(project_path, cfg["git_log_count"])
        if ctx.git_log:
            commit_count = ctx.git_log.count("\n") + 1
            await emit_context_event(
                task_id, "file_read", "git log",
                detail=f"{commit_count} commits", status="done",
            )

        # Entry points
        for ep in _detect_entry_points(project_path, ctx.claude_md, cfg["max_entry_points"]):
            body = _read_lines(ep, cfg["entry_point_lines"])
            rel = str(ep.relative_to(project_path))
            ctx.entry_points[rel] = body
            await emit_context_event(
                task_id, "file_read", rel,
                detail=f"{body.count(chr(10))} lines", status="done",
            )

        ctx.loaded_at = time.time()
        _contexts[project_path] = ctx

        await emit_context_event(
            task_id, final_stage,
            "Context refreshed" if reason == "refresh" else "Context ready",
            detail=f"{len(ctx.entry_points)} entry points",
            status="done",
        )

    # First-load only: attach a file watcher.
    if project_path not in _watchers:
        _start_watcher(project_path)

    return ctx


async def refresh(project_path: Path | None = None) -> ProjectContext | None:
    """Re-read warm context. Defaults to the active (most-recently-loaded) project."""
    path = Path(project_path).resolve() if project_path else _active_path
    if path is None:
        return None
    return await load(path, reason="refresh")


def get(project_path: Path) -> ProjectContext | None:
    return _contexts.get(Path(project_path).resolve())


def get_active() -> ProjectContext | None:
    if _active_path is None:
        return None
    return _contexts.get(_active_path)


# ---------------------------------------------------------------------------
# File watcher — debounced refresh on CLAUDE.md / README / entry-point edits
# ---------------------------------------------------------------------------

class _DebouncedRefreshHandler(FileSystemEventHandler):
    """Watchdog handler: debounces fs events, triggers refresh on the asyncio loop."""

    def __init__(self, project_path: Path, loop: asyncio.AbstractEventLoop, debounce_ms: int):
        self._project_path = project_path
        self._loop = loop
        self._debounce = debounce_ms / 1000.0
        self._timer: asyncio.TimerHandle | None = None

    def _watched_relpath(self, src_path: str) -> str | None:
        try:
            rel = Path(src_path).resolve().relative_to(self._project_path)
        except ValueError:
            return None
        relstr = str(rel)
        if relstr in {"CLAUDE.md", "README.md"}:
            return relstr
        ctx = _contexts.get(self._project_path)
        if ctx and relstr in ctx.entry_points:
            return relstr
        return None

    def on_modified(self, event):
        if event.is_directory:
            return
        if self._watched_relpath(event.src_path) is None:
            return
        # Cross-thread: schedule debounce on the asyncio loop.
        self._loop.call_soon_threadsafe(self._schedule)

    def _schedule(self):
        if self._timer is not None:
            self._timer.cancel()
        self._timer = self._loop.call_later(self._debounce, self._fire)

    def _fire(self):
        self._timer = None
        asyncio.ensure_future(self._refresh_safely())

    async def _refresh_safely(self):
        try:
            await load(self._project_path, reason="refresh")
        except Exception as e:
            log.error(f"Watcher refresh failed for {self._project_path}: {e}")


def _start_watcher(project_path: Path) -> None:
    """Attach a recursive watchdog observer for this project. Idempotent."""
    if project_path in _watchers:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning(f"No running loop; can't attach watcher to {project_path}")
        return
    cfg = _warm_config()
    handler = _DebouncedRefreshHandler(project_path, loop, cfg["fs_debounce_ms"])
    observer = Observer()
    observer.schedule(handler, str(project_path), recursive=True)
    observer.daemon = True
    observer.start()
    _watchers[project_path] = {"observer": observer, "handler": handler}
    log.info(f"watchdog: started watcher for {project_path}")


def stop_watcher(project_path: Path) -> None:
    """Stop a single project's watcher."""
    entry = _watchers.pop(project_path, None)
    if entry:
        try:
            entry["observer"].stop()
            entry["observer"].join(timeout=1)
        except Exception:
            pass


def stop_all_watchers() -> None:
    for path in list(_watchers.keys()):
        stop_watcher(path)
