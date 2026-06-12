"""Risk-gated executor (Stage D).

`SafeExecutor` wraps any Stage C `ActionExecutor` and applies the safety model
around every capability:

  * Tier 0 (reads, lists, opens, navigation) runs straight through.
  * Tier 1 (delete/move/overwrite, drive apps, keystrokes, scripts) requires an
    explicit confirmation via the ConfirmationManager before the delegate runs.
  * The kill switch is checked before every action (and again after a Tier 1
    confirmation, since the user may have hit it while deciding) — engaged means
    the action is refused.

Deletes still go to Trash (the AppleScript backend's delete_file does that); the
confirm card just makes it explicit. The whole thing is a decorator, so callers
keep talking to the `ActionExecutor` interface and never see the gating.
"""

from __future__ import annotations

from typing import Optional

import safety
from action_executor import ActionExecutor, ActionResult, Capability


class SafeExecutor(ActionExecutor):
    name = "safe"

    def __init__(
        self,
        delegate: ActionExecutor,
        *,
        confirmations: Optional[safety.ConfirmationManager] = None,
        kill_switch: Optional[safety.KillSwitch] = None,
    ) -> None:
        self._delegate = delegate
        self._confirmations = confirmations or safety.confirmations
        self._kill = kill_switch or safety.kill_switch
        self.name = f"safe({delegate.name})"

    # --- gate ------------------------------------------------------------
    async def _guard(
        self,
        capability: Capability,
        *,
        summary: str,
        path: Optional[str] = None,
        count: int = 1,
        press_enter: bool = False,
        targets: Optional[list] = None,
    ) -> Optional[ActionResult]:
        """Return an ActionResult to SHORT-CIRCUIT (kill switch / denied), or
        None to allow the delegate to run."""
        if self._kill.is_engaged():
            return ActionResult.failure(capability, error="kill_switch", message="Halted, sir.")

        decision = safety.classify(capability, path=path, count=count, press_enter=press_enter)
        if decision.tier == safety.RiskTier.AUTO:
            return None

        allowed = await self._confirmations.request(
            summary=summary,
            detail=decision.reason,
            targets=targets or ([path] if path else []),
            tier=int(decision.tier),
            warning=decision.warning,
        )
        if not allowed:
            return ActionResult.failure(capability, error="denied", message="Cancelled, sir.")
        # Re-check: the user may have engaged the kill switch while deciding.
        if self._kill.is_engaged():
            return ActionResult.failure(capability, error="kill_switch", message="Halted, sir.")
        return None

    # --- apps ------------------------------------------------------------
    async def open_app(self, app: str, *, task_id: Optional[str] = None) -> ActionResult:
        blocked = await self._guard(Capability.OPEN_APP, summary=f"Open {app}")
        return blocked or await self._delegate.open_app(app, task_id=task_id)

    async def open_path(self, path: str, *, task_id: Optional[str] = None) -> ActionResult:
        blocked = await self._guard(Capability.OPEN_PATH, summary=f"Open {path}", path=path)
        return blocked or await self._delegate.open_path(path, task_id=task_id)

    async def run_app_command(self, app: str, command: str, *, task_id: Optional[str] = None) -> ActionResult:
        blocked = await self._guard(Capability.RUN_APP_COMMAND, summary=f"Tell {app} to: {command}", targets=[app])
        return blocked or await self._delegate.run_app_command(app, command, task_id=task_id)

    async def send_keystroke(self, app: str, text: str, *, press_enter: bool = False, task_id: Optional[str] = None) -> ActionResult:
        verb = "Type and send" if press_enter else "Type"
        blocked = await self._guard(
            Capability.SEND_KEYSTROKE,
            summary=f"{verb} into {app or 'the active app'}: {text[:80]}",
            press_enter=press_enter, targets=[app] if app else [],
        )
        return blocked or await self._delegate.send_keystroke(app, text, press_enter=press_enter, task_id=task_id)

    # --- files -----------------------------------------------------------
    async def read_file(self, path: str) -> ActionResult:
        blocked = await self._guard(Capability.READ_FILE, summary=f"Read {path}", path=path)
        return blocked or await self._delegate.read_file(path)

    async def write_file(self, path: str, content: str) -> ActionResult:
        blocked = await self._guard(Capability.WRITE_FILE, summary=f"Write {path}", path=path, targets=[path])
        return blocked or await self._delegate.write_file(path, content)

    async def move_file(self, src: str, dst: str) -> ActionResult:
        blocked = await self._guard(Capability.MOVE_FILE, summary=f"Move {src} -> {dst}", path=src, targets=[src, dst])
        return blocked or await self._delegate.move_file(src, dst)

    async def delete_file(self, path: str) -> ActionResult:
        blocked = await self._guard(Capability.DELETE_FILE, summary=f"Delete {path} (to Trash)", path=path, targets=[path])
        return blocked or await self._delegate.delete_file(path)

    async def list_folder(self, path: str) -> ActionResult:
        blocked = await self._guard(Capability.LIST_FOLDER, summary=f"List {path}", path=path)
        return blocked or await self._delegate.list_folder(path)

    # --- web -------------------------------------------------------------
    async def navigate(self, url: str, *, browser: str = "chrome") -> ActionResult:
        blocked = await self._guard(Capability.NAVIGATE, summary=f"Open {url}")
        return blocked or await self._delegate.navigate(url, browser=browser)

    # --- escape hatch ----------------------------------------------------
    async def run_script(self, script: str) -> ActionResult:
        blocked = await self._guard(Capability.RUN_SCRIPT, summary=f"Run a script: {script[:80]}")
        return blocked or await self._delegate.run_script(script)

    # --- universal control (UC1) -----------------------------------------
    async def observe_ui(self, *, app: Optional[str] = None, max_elements: int = 250,
                         task_id: Optional[str] = None) -> ActionResult:
        # Tier 0 — observation runs straight through (the guard returns None for
        # AUTO), but still respects the kill switch.
        blocked = await self._guard(Capability.OBSERVE_UI, summary=f"Read the screen ({app or 'frontmost app'})")
        return blocked or await self._delegate.observe_ui(app=app, max_elements=max_elements, task_id=task_id)

    async def click_element(self, *, ref: Optional[str] = None, point: Optional[tuple] = None,
                           app: Optional[str] = None, task_id: Optional[str] = None) -> ActionResult:
        where = f"element {ref}" if ref else (f"point {point}" if point else "a control")
        blocked = await self._guard(Capability.CLICK_ELEMENT, summary=f"Click {where}",
                                    targets=[ref] if ref else [])
        return blocked or await self._delegate.click_element(ref=ref, point=point, app=app, task_id=task_id)

    async def key_combo(self, combo: str, *, app: Optional[str] = None,
                       task_id: Optional[str] = None) -> ActionResult:
        blocked = await self._guard(Capability.KEY_COMBO,
                                    summary=f"Press {combo}" + (f" in {app}" if app else ""),
                                    targets=[app] if app else [])
        return blocked or await self._delegate.key_combo(combo, app=app, task_id=task_id)

    # --- introspection (no gate) ----------------------------------------
    async def is_app_scriptable(self, app: str) -> bool:
        return await self._delegate.is_app_scriptable(app)
