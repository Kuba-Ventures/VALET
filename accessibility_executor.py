"""macOS Accessibility / synthetic-input backend (Phase K, K1).

The AppleScript backend can only drive apps that ship a scripting dictionary.
This backend reaches the rest: it activates an app and posts synthetic keyboard
events (Quartz CGEvents) to whatever is focused — so VALET can type into apps
that AppleScript refuses.

It is the first fallback layer under the portable `ActionExecutor` interface;
`CompositeExecutor` routes a non-scriptable app's keystrokes here. Only the
input capabilities are implemented; file ops / navigate / run_script stay
not_supported (the composite keeps those on AppleScript).

Requires pyobjc (Quartz + AppKit) and macOS Accessibility permission. With
pyobjc absent every method returns a clean `not_supported` — it never raises,
and importing this module never fails.

STATUS: the CGEvent paths need on-device validation (Accessibility permission +
real apps) — they cannot be exercised without a Mac GUI session. The structure,
routing, and import-safety are what's verified here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from action_executor import ActionExecutor, ActionResult, Capability

log = logging.getLogger("valet.accessibility")

# Guarded optional dependency. If pyobjc isn't present (e.g. dev box without it),
# the backend is inert rather than broken.
try:
    import Quartz  # type: ignore
    from AppKit import NSWorkspace  # type: ignore

    _PYOBJC = True
except Exception:  # ImportError, or a non-macOS host
    _PYOBJC = False

_RETURN_KEYCODE = 36  # virtual keycode for Return


def _activate_app(app: str) -> bool:
    """Bring `app` to the front so synthetic events land in it. Best-effort."""
    try:
        ws = NSWorkspace.sharedWorkspace()
        for running in ws.runningApplications():
            name = running.localizedName() or ""
            if name.lower() == app.lower():
                running.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
                return True
        # Not running yet — launch it (also activates).
        return bool(ws.launchApplication_(app))
    except Exception as e:
        log.warning("activate %s failed: %s", app, e)
        return False


def _post_text(text: str) -> None:
    """Post each character as a keyboard event to the focused app."""
    for ch in text:
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _post_return() -> None:
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, _RETURN_KEYCODE, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


class AccessibilityExecutor(ActionExecutor):
    """Synthetic-input backend for apps AppleScript can't drive."""

    name = "macos-accessibility"

    def _unavailable(self, capability: Capability) -> ActionResult:
        return ActionResult.not_supported(
            capability,
            reason="pyobjc (Quartz/AppKit) not installed",
            message="I can't reach that app on this Mac yet, sir.",
        )

    # --- Input (what this backend adds) ----------------------------------

    async def open_app(self, app: str, *, task_id: Optional[str] = None) -> ActionResult:
        if not _PYOBJC:
            return self._unavailable(Capability.OPEN_APP)
        ok = await asyncio.to_thread(_activate_app, app)
        if ok:
            return ActionResult.success(Capability.OPEN_APP, message=f"Opened {app}, sir.")
        return ActionResult.failure(Capability.OPEN_APP, error="activate failed",
                                    message=f"I couldn't open {app}, sir.")

    async def send_keystroke(self, app: str, text: str, *, press_enter: bool = False,
                             task_id: Optional[str] = None) -> ActionResult:
        if not _PYOBJC:
            return self._unavailable(Capability.SEND_KEYSTROKE)

        def _do() -> bool:
            if not _activate_app(app):
                return False
            _post_text(text)
            if press_enter:
                _post_return()
            return True

        ok = await asyncio.to_thread(_do)
        if ok:
            return ActionResult.success(Capability.SEND_KEYSTROKE,
                                        message="Done, sir.", backend=self.name)
        return ActionResult.failure(Capability.SEND_KEYSTROKE, error="post failed",
                                    message=f"I couldn't type into {app}, sir.")

    # --- Capabilities this backend does NOT cover (composite uses AppleScript) ---

    async def open_path(self, path: str, *, task_id: Optional[str] = None) -> ActionResult:
        return ActionResult.not_supported(Capability.OPEN_PATH,
                                          reason="accessibility backend handles input only")

    async def run_app_command(self, app: str, command: str, *, task_id: Optional[str] = None) -> ActionResult:
        return ActionResult.not_supported(Capability.RUN_APP_COMMAND,
                                          reason="accessibility backend handles input only")

    async def read_file(self, path: str) -> ActionResult:
        return ActionResult.not_supported(Capability.READ_FILE, reason="not an input capability")

    async def write_file(self, path: str, content: str) -> ActionResult:
        return ActionResult.not_supported(Capability.WRITE_FILE, reason="not an input capability")

    async def move_file(self, src: str, dst: str) -> ActionResult:
        return ActionResult.not_supported(Capability.MOVE_FILE, reason="not an input capability")

    async def delete_file(self, path: str) -> ActionResult:
        return ActionResult.not_supported(Capability.DELETE_FILE, reason="not an input capability")

    async def list_folder(self, path: str) -> ActionResult:
        return ActionResult.not_supported(Capability.LIST_FOLDER, reason="not an input capability")

    async def navigate(self, url: str, *, browser: str = "chrome") -> ActionResult:
        return ActionResult.not_supported(Capability.NAVIGATE, reason="not an input capability")

    async def run_script(self, script: str) -> ActionResult:
        return ActionResult.not_supported(Capability.RUN_SCRIPT, reason="no script engine in this backend")

    async def is_app_scriptable(self, app: str) -> bool:
        return False  # this backend uses synthetic input, not a scripting dictionary

    def capabilities(self) -> set:
        # What this backend can actually do today.
        return {Capability.OPEN_APP, Capability.SEND_KEYSTROKE}
