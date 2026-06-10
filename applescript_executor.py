"""macOS AppleScript backend for the ActionExecutor interface (Stage C).

Drives scriptable apps via their dictionaries (osascript), Finder for file
operations, and `open` via Launch Services. Reuses the existing, battle-tested
helpers in actions.py where they already do the right thing; adds the file
read/write/move/list capabilities and scriptability detection.

Non-scriptable apps return ActionResult.not_supported(...) (never raise) and the
attempt is logged, so usage data shows where the AppleScript-only v1 floor hurts
and which fallback layer (Accessibility UI scripting, then vision) to build next.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import actions
from action_executor import ActionExecutor, ActionResult, Capability

log = logging.getLogger("jarvis.executor")

# Cap automatic file reads so a stray "read this" on a huge file can't blow up
# the context or memory. Larger reads should chunk (a later concern).
_MAX_READ_BYTES = 1_000_000


def _unsupported_log(capability: Capability, target: str, reason: str) -> None:
    """Single choke point for logging not-supported attempts (usage signal)."""
    log.info(f"action_unsupported capability={capability.value} target={target!r} reason={reason!r}")


class AppleScriptExecutor(ActionExecutor):
    name = "macos-applescript"

    # ------------------------------------------------------------------ apps
    async def open_app(self, app: str, *, task_id: Optional[str] = None) -> ActionResult:
        res = await actions.open_app_or_path(app, task_id=task_id)
        return self._adapt(Capability.OPEN_APP, res)

    async def open_path(self, path: str, *, task_id: Optional[str] = None) -> ActionResult:
        res = await actions.open_app_or_path(path, task_id=task_id)
        return self._adapt(Capability.OPEN_PATH, res)

    async def send_keystroke(self, app: str, text: str, *, press_enter: bool = False, task_id: Optional[str] = None) -> ActionResult:
        target = f"{app} ||| {text}" if app else text
        res = await actions.type_into_app(target, press_enter=press_enter, task_id=task_id)
        return self._adapt(Capability.SEND_KEYSTROKE, res)

    async def run_app_command(self, app: str, command: str, *, task_id: Optional[str] = None) -> ActionResult:
        # Proactively gate on scriptability so non-scriptable apps get a clean,
        # logged not-supported instead of a cryptic AppleScript error.
        if not await self.is_app_scriptable(app):
            reason = f"{app} does not expose an AppleScript dictionary"
            _unsupported_log(Capability.RUN_APP_COMMAND, app, reason)
            return ActionResult.not_supported(
                Capability.RUN_APP_COMMAND,
                reason=reason,
                message=f"{app} can't be controlled that way yet, sir.",
                app=app,
            )
        script = f'tell application "{app}" to {command}'
        out = await self.run_script(script)
        return ActionResult(
            ok=out.ok,
            capability=Capability.RUN_APP_COMMAND,
            data=out.data,
            message=out.message,
            error=out.error,
            meta={"app": app, "command": command},
        )

    # ----------------------------------------------------------------- files
    async def read_file(self, path: str) -> ActionResult:
        p = Path(path).expanduser()
        if not p.exists():
            return ActionResult.failure(Capability.READ_FILE, error="not_found", message=f"I can't find {path}, sir.")
        if p.is_dir():
            return ActionResult.failure(Capability.READ_FILE, error="is_directory", message=f"{path} is a folder, sir.")
        try:
            size = p.stat().st_size
            truncated = size > _MAX_READ_BYTES
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_MAX_READ_BYTES)
            return ActionResult.success(
                Capability.READ_FILE,
                data=content,
                message=f"Read {p.name}, sir.",
                path=str(p), bytes=size, truncated=truncated,
            )
        except Exception as e:
            return ActionResult.failure(Capability.READ_FILE, error=str(e)[:200], message="I couldn't read that file, sir.")

    async def write_file(self, path: str, content: str) -> ActionResult:
        p = Path(path).expanduser()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return ActionResult.success(Capability.WRITE_FILE, message=f"Wrote {p.name}, sir.", path=str(p), bytes=len(content.encode("utf-8")))
        except Exception as e:
            return ActionResult.failure(Capability.WRITE_FILE, error=str(e)[:200], message="I couldn't write that file, sir.")

    async def move_file(self, src: str, dst: str) -> ActionResult:
        s = Path(src).expanduser()
        d = Path(dst).expanduser()
        if not s.exists():
            return ActionResult.failure(Capability.MOVE_FILE, error="not_found", message=f"I can't find {src}, sir.")
        try:
            shutil.move(str(s), str(d))
            return ActionResult.success(Capability.MOVE_FILE, message=f"Moved {s.name}, sir.", src=str(s), dst=str(d))
        except Exception as e:
            return ActionResult.failure(Capability.MOVE_FILE, error=str(e)[:200], message="I couldn't move that, sir.")

    async def delete_file(self, path: str) -> ActionResult:
        # Delegate to actions.delete_file — moves to Trash via Finder (recoverable).
        res = await actions.delete_file(path)
        return self._adapt(Capability.DELETE_FILE, res)

    async def list_folder(self, path: str) -> ActionResult:
        p = Path(path).expanduser()
        if not p.exists():
            return ActionResult.failure(Capability.LIST_FOLDER, error="not_found", message=f"I can't find {path}, sir.")
        if not p.is_dir():
            return ActionResult.failure(Capability.LIST_FOLDER, error="not_directory", message=f"{path} isn't a folder, sir.")
        try:
            entries = []
            with os.scandir(p) as it:
                for e in it:
                    try:
                        st = e.stat()
                        entries.append({"name": e.name, "is_dir": e.is_dir(), "size": st.st_size, "modified": st.st_mtime})
                    except OSError:
                        entries.append({"name": e.name, "is_dir": e.is_dir(), "size": None, "modified": None})
            entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return ActionResult.success(
                Capability.LIST_FOLDER,
                data=entries,
                message=f"{len(entries)} items in {p.name or path}, sir.",
                path=str(p), count=len(entries),
            )
        except Exception as e:
            return ActionResult.failure(Capability.LIST_FOLDER, error=str(e)[:200], message="I couldn't list that folder, sir.")

    # ------------------------------------------------------------------- web
    async def navigate(self, url: str, *, browser: str = "chrome") -> ActionResult:
        res = await actions.open_browser(url, browser=browser)
        return self._adapt(Capability.NAVIGATE, res)

    # ---------------------------------------------------------- escape hatch
    async def run_script(self, script: str) -> ActionResult:
        res = await actions.run_applescript(script)
        return self._adapt(Capability.RUN_SCRIPT, res)

    # --------------------------------------------------------- introspection
    async def is_app_scriptable(self, app: str) -> bool:
        bundle = await self._find_app_bundle(app)
        if not bundle:
            return False
        res_dir = Path(bundle) / "Contents" / "Resources"
        try:
            if any(res_dir.glob("*.sdef")):
                return True
        except OSError:
            pass
        # Fall back to the Info.plist scripting flags.
        info = Path(bundle) / "Contents" / "Info.plist"
        for key in ("NSAppleScriptEnabled", "OSAScriptingDefinition"):
            val = await self._plist_read(info, key)
            if val and val.strip().lower() not in ("", "0", "no", "false"):
                return True
        return False

    # --------------------------------------------------------------- helpers
    async def _find_app_bundle(self, app: str) -> Optional[str]:
        """Resolve an app name to its .app bundle path WITHOUT launching it."""
        name = app.replace('"', '')
        script = f'POSIX path of (path to application "{name}")'
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            if proc.returncode == 0:
                path = out.decode().strip()
                return path or None
        except Exception:
            pass
        return None

    async def _plist_read(self, plist: Path, key: str) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "defaults", "read", str(plist.with_suffix("")), key,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            if proc.returncode == 0:
                return out.decode().strip()
        except Exception:
            pass
        return None

    def _adapt(self, capability: Capability, res: dict) -> ActionResult:
        """Adapt an actions.py dict ({success, confirmation, ...}) to ActionResult."""
        ok = bool(res.get("success"))
        message = res.get("confirmation", "") or ""
        data = res.get("output", res.get("data"))
        return ActionResult(
            ok=ok,
            capability=capability,
            data=data,
            message=message,
            error=None if ok else (res.get("error") or message or "failed"),
            meta={k: v for k, v in res.items() if k not in ("success", "confirmation", "output", "data", "error")},
        )


# Module-level singleton — the default executor for this (macOS) build.
executor: ActionExecutor = AppleScriptExecutor()
