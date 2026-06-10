"""Risk-tiered safety model (Stage D).

Every action runs through one of two tiers:

- Tier 0 (automatic): reads, lists, opening apps, navigating — run without asking.
- Tier 1 (confirm first): delete/move/overwrite files, bulk operations over a
  small threshold, driving apps, sending keystrokes, running scripts, anything
  outbound or irreversible — require an explicit confirmation.

This module owns three things, all backend-pure and unit-testable:
  * `classify(...)`  — the tier decision for a capability + context.
  * `KillSwitch`     — an always-available halt for in-progress work + the loop.
  * `ConfirmationManager` — the async request/await round-trip to the UI.

The UI (confirm card, kill button) and the WebSocket plumbing are wired on top
in server.py; `SafeExecutor` (safe_executor.py) applies all of this around the
Stage C `ActionExecutor`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Awaitable, Callable, Optional

from action_executor import Capability

log = logging.getLogger("jarvis.safety")

# Bulk file operations affecting more than this many targets always confirm.
BULK_THRESHOLD = 5

# Capabilities that are irreversible / outbound / system-controlling by nature.
_ALWAYS_TIER1 = {
    Capability.DELETE_FILE,
    Capability.MOVE_FILE,
    Capability.RUN_SCRIPT,
    Capability.RUN_APP_COMMAND,
    Capability.SEND_KEYSTROKE,
}

# Capabilities that are always safe (reads / opens / navigation).
_ALWAYS_TIER0 = {
    Capability.READ_FILE,
    Capability.LIST_FOLDER,
    Capability.OPEN_APP,
    Capability.OPEN_PATH,
    Capability.NAVIGATE,
}

# Protected roots: writing/moving/deleting under these warrants an explicit
# warning even though the action is already Tier 1.
_PROTECTED_PREFIXES = (
    "/System", "/usr", "/bin", "/sbin", "/Library", "/private",
    str(Path.home() / "Library"),
)


class RiskTier(IntEnum):
    AUTO = 0      # Tier 0 — runs automatically
    CONFIRM = 1   # Tier 1 — requires confirmation


@dataclass
class RiskDecision:
    tier: RiskTier
    reason: str
    warning: Optional[str] = None  # extra caution note (protected/system paths)


def _is_protected(path: Optional[str]) -> bool:
    if not path:
        return False
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        resolved = str(Path(path).expanduser())
    return any(resolved == p or resolved.startswith(p + "/") for p in _PROTECTED_PREFIXES)


def classify(
    capability: Capability,
    *,
    path: Optional[str] = None,
    count: int = 1,
    press_enter: bool = False,
) -> RiskDecision:
    """Decide the risk tier for a capability invocation."""
    # Bulk anything over the threshold confirms, regardless of capability.
    if count > BULK_THRESHOLD:
        return RiskDecision(RiskTier.CONFIRM, f"bulk operation on {count} items")

    warning = "Protected/system location — proceed with care." if _is_protected(path) else None

    if capability in _ALWAYS_TIER1:
        if _is_protected(path):
            return RiskDecision(RiskTier.CONFIRM, f"{capability.value} in a protected location", warning)
        return RiskDecision(RiskTier.CONFIRM, capability.value)

    if capability is Capability.WRITE_FILE:
        # Overwriting an existing file confirms; creating a new file is Tier 0.
        exists = bool(path) and Path(path).expanduser().exists()
        if exists or _is_protected(path):
            return RiskDecision(RiskTier.CONFIRM, "overwrite existing file", warning)
        return RiskDecision(RiskTier.AUTO, "create new file")

    if capability in _ALWAYS_TIER0:
        return RiskDecision(RiskTier.AUTO, capability.value)

    # Unknown capability: fail safe to confirm.
    return RiskDecision(RiskTier.CONFIRM, f"unclassified capability {capability.value}")


class KillSwitchEngaged(Exception):
    """Raised when work is attempted while the kill switch is engaged."""


class KillSwitch:
    """Always-available halt. Engaging it aborts in-progress work (which polls
    `check()`/`is_engaged()`) and the assistant loop until reset."""

    def __init__(self) -> None:
        self._engaged = False

    def engage(self) -> None:
        if not self._engaged:
            log.warning("KILL SWITCH ENGAGED — halting actions and assistant loop")
        self._engaged = True

    def reset(self) -> None:
        if self._engaged:
            log.info("kill switch reset")
        self._engaged = False

    def is_engaged(self) -> bool:
        return self._engaged

    def check(self) -> None:
        if self._engaged:
            raise KillSwitchEngaged("Kill switch is engaged.")


@dataclass
class PendingConfirmation:
    id: str
    summary: str
    detail: str
    targets: list
    tier: int
    warning: Optional[str]
    future: "asyncio.Future[bool]" = field(repr=False, default=None)


class ConfirmationManager:
    """Async request/await for Tier 1 confirmations.

    server.py registers a `sender` (broadcasts a confirm_request over the
    WebSocket) and routes incoming confirm_response messages to `resolve()`.
    `request()` blocks until the user answers (or the timeout / kill switch).
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}
        self._sender: Optional[Callable[[dict], Awaitable[None]]] = None

    def set_sender(self, sender: Callable[[dict], Awaitable[None]]) -> None:
        self._sender = sender

    @property
    def pending(self) -> dict[str, PendingConfirmation]:
        return self._pending

    async def request(
        self,
        *,
        summary: str,
        detail: str = "",
        targets: Optional[list] = None,
        tier: int = int(RiskTier.CONFIRM),
        warning: Optional[str] = None,
        timeout: float = 120.0,
    ) -> bool:
        """Ask the user to confirm. Returns True (allow) / False (deny/timeout)."""
        cid = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        fut: "asyncio.Future[bool]" = loop.create_future()
        pc = PendingConfirmation(cid, summary, detail, targets or [], tier, warning, fut)
        self._pending[cid] = pc

        if self._sender is not None:
            try:
                await self._sender({
                    "type": "confirm_request",
                    "id": cid,
                    "summary": summary,
                    "detail": detail,
                    "targets": targets or [],
                    "tier": tier,
                    "warning": warning,
                })
            except Exception as e:
                log.error(f"confirm send failed: {e}")
                self._pending.pop(cid, None)
                return False
        else:
            # No UI attached (e.g. headless/tests) — fail safe to deny.
            self._pending.pop(cid, None)
            return False

        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            log.info(f"confirmation {cid} timed out -> deny")
            return False
        finally:
            self._pending.pop(cid, None)

    def resolve(self, cid: str, allow: bool) -> bool:
        """Resolve a pending confirmation from a UI response. Returns True if a
        matching pending request was found."""
        pc = self._pending.get(cid)
        if pc and pc.future and not pc.future.done():
            pc.future.set_result(bool(allow))
            return True
        return False

    def cancel_all(self, allow: bool = False) -> None:
        """Resolve every pending confirmation (used when the kill switch fires)."""
        for pc in list(self._pending.values()):
            if pc.future and not pc.future.done():
                pc.future.set_result(allow)


# Module-level singletons shared across the app.
kill_switch = KillSwitch()
confirmations = ConfirmationManager()
