"""Portable Mac-control interface (Stage C).

`ActionExecutor` describes the capabilities the assistant needs — open an app,
read/write/move/delete a file, list a folder, drive a scriptable app, navigate a
browser, send keystrokes — independent of the operating system. The macOS
AppleScript backend lives in `applescript_executor.py`; a future Windows/Linux
backend (or an Accessibility/vision backend) is a swap behind this interface,
not a rewrite of the callers.

Risk tiering and confirmations are intentionally NOT here — that is Stage D,
which wraps this interface. Stage C only exposes capabilities and reports, in a
structured `ActionResult`, when a target is not supported on the current backend
(e.g. a non-scriptable app under the AppleScript-only v1 floor).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Capability(str, Enum):
    """Stable capability identifiers — used for metering/logging which actions
    (and which fallback layers) the product needs next."""

    OPEN_APP = "open_app"
    OPEN_PATH = "open_path"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    MOVE_FILE = "move_file"
    DELETE_FILE = "delete_file"
    LIST_FOLDER = "list_folder"
    RUN_APP_COMMAND = "run_app_command"
    SEND_KEYSTROKE = "send_keystroke"
    NAVIGATE = "navigate"
    RUN_SCRIPT = "run_script"

    # Phase K (UC1): universal control primitives. The AppleScript backend
    # leaves these not_supported; the Accessibility backend implements them.
    OBSERVE_UI = "observe_ui"        # enumerate the focused window's AX tree (read)
    CLICK_ELEMENT = "click_element"  # click an element by ref or point (input)
    KEY_COMBO = "key_combo"          # send a modifier+key chord (input)


@dataclass
class ActionResult:
    """Uniform result for every capability.

    - ok: the action succeeded.
    - supported: the capability/target is supported on this backend. When False
      (e.g. a non-scriptable app in v1), `reason` explains why and the attempt is
      logged so the usage data shows where the AppleScript-only floor hurts.
    - data: capability-specific payload (file contents, folder listing, …).
    - message: a short, user-facing line (butler voice) describing the outcome.
    - error / reason: machine/diagnostic detail.
    """

    ok: bool
    capability: Capability
    supported: bool = True
    data: Any = None
    message: str = ""
    error: Optional[str] = None
    reason: Optional[str] = None
    meta: dict = field(default_factory=dict)

    @classmethod
    def success(cls, capability: Capability, *, data: Any = None, message: str = "", **meta) -> "ActionResult":
        return cls(ok=True, capability=capability, data=data, message=message, meta=meta)

    @classmethod
    def failure(cls, capability: Capability, *, error: str, message: str = "", **meta) -> "ActionResult":
        return cls(ok=False, capability=capability, error=error, message=message, meta=meta)

    @classmethod
    def not_supported(cls, capability: Capability, *, reason: str, message: str = "", **meta) -> "ActionResult":
        return cls(
            ok=False,
            capability=capability,
            supported=False,
            reason=reason,
            message=message or "That isn't supported on this Mac yet, sir.",
            meta=meta,
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "capability": self.capability.value,
            "supported": self.supported,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "reason": self.reason,
            "meta": self.meta,
        }


@dataclass
class UIElement:
    """One node in a focused window's accessibility tree (UC1 observation).

    `ref` is a stable handle for the duration of the observation snapshot that
    produced it — pass it back to `click_element(ref=...)`. `frame` is
    [x, y, w, h] in global screen points (top-left origin).
    """

    ref: str
    role: str
    title: str = ""
    value: str = ""
    enabled: bool = True
    frame: Optional[list] = None  # [x, y, w, h]

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "role": self.role,
            "title": self.title,
            "value": self.value,
            "enabled": self.enabled,
            "frame": self.frame,
        }


class ActionExecutor(ABC):
    """OS-independent control surface. Implementations drive the host machine.

    Every method is async and returns an `ActionResult`. Implementations must
    return `ActionResult.not_supported(...)` (never raise) when a target is
    outside what the backend can do, so callers and metering get a clean signal
    rather than an exception.
    """

    #: Human-readable backend name, e.g. "macos-applescript".
    name: str = "abstract"

    # --- Apps -------------------------------------------------------------
    @abstractmethod
    async def open_app(self, app: str, *, task_id: Optional[str] = None) -> ActionResult:
        """Launch an application by name."""

    @abstractmethod
    async def open_path(self, path: str, *, task_id: Optional[str] = None) -> ActionResult:
        """Open a file or folder in the OS file browser."""

    @abstractmethod
    async def run_app_command(self, app: str, command: str, *, task_id: Optional[str] = None) -> ActionResult:
        """Drive a scriptable app (one command). Returns not_supported for apps
        without a scripting interface."""

    @abstractmethod
    async def send_keystroke(self, app: str, text: str, *, press_enter: bool = False, task_id: Optional[str] = None) -> ActionResult:
        """Type text into an app (and optionally press Return)."""

    # --- Files ------------------------------------------------------------
    @abstractmethod
    async def read_file(self, path: str) -> ActionResult:
        """Read a text file. Reads are automatic (no confirmation)."""

    @abstractmethod
    async def write_file(self, path: str, content: str) -> ActionResult:
        """Write/overwrite a text file. (Risk gating is Stage D's concern.)"""

    @abstractmethod
    async def move_file(self, src: str, dst: str) -> ActionResult:
        """Move/rename a file."""

    @abstractmethod
    async def delete_file(self, path: str) -> ActionResult:
        """Delete a file. Backends must send to Trash, never permanently erase."""

    @abstractmethod
    async def list_folder(self, path: str) -> ActionResult:
        """List a folder's entries with basic metadata."""

    # --- Web --------------------------------------------------------------
    @abstractmethod
    async def navigate(self, url: str, *, browser: str = "chrome") -> ActionResult:
        """Open a URL in the user's browser."""

    # --- Escape hatch -----------------------------------------------------
    @abstractmethod
    async def run_script(self, script: str) -> ActionResult:
        """Run a raw backend-native script (AppleScript on macOS). Full control —
        use sparingly; a portable caller should prefer the typed capabilities."""

    # --- Introspection ----------------------------------------------------
    @abstractmethod
    async def is_app_scriptable(self, app: str) -> bool:
        """Whether `app` exposes a scripting interface this backend can drive."""

    # --- Universal control (UC1) ------------------------------------------
    # Concrete defaults (NOT abstract) so existing backends — notably the
    # AppleScript executor — inherit a clean not_supported and stay untouched.
    # The Accessibility backend overrides these; the composite routes a
    # not_supported result to it.

    async def observe_ui(
        self, *, app: Optional[str] = None, max_elements: int = 200,
        task_id: Optional[str] = None,
    ) -> ActionResult:
        """Enumerate the focused window's accessibility tree into a list of
        `UIElement` dicts (in `data["elements"]`). Read-only (Tier 0)."""
        return ActionResult.not_supported(
            Capability.OBSERVE_UI,
            reason=f"{self.name} cannot read the accessibility tree",
        )

    async def click_element(
        self, *, ref: Optional[str] = None, point: Optional[tuple] = None,
        app: Optional[str] = None, task_id: Optional[str] = None,
    ) -> ActionResult:
        """Click a UI element identified by `ref` (from a prior observe) or by an
        absolute screen `point` (x, y). Input action (Tier 1)."""
        return ActionResult.not_supported(
            Capability.CLICK_ELEMENT,
            reason=f"{self.name} cannot synthesize a click",
        )

    async def key_combo(
        self, combo: str, *, app: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> ActionResult:
        """Send a modifier chord like 'cmd+s' or 'cmd+shift+4'. Input (Tier 1)."""
        return ActionResult.not_supported(
            Capability.KEY_COMBO,
            reason=f"{self.name} cannot synthesize key combos",
        )
