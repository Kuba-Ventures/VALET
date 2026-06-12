"""Composite control backend (Phase K, K1).

Routes each capability to a primary backend (AppleScript) and falls back to a
secondary backend (Accessibility / synthetic input) when the primary reports the
target is `not_supported` — e.g. a non-scriptable app's keystrokes. This is the
portable `ActionExecutor` interface paying off: broadening control is a routing
layer, not a rewrite of the callers.

The fallback fires ONLY on `supported is False` (a clean "this backend can't do
it" signal), never on a genuine failure — a real error from the primary is
returned as-is, so we don't retry destructive things twice.
"""

from __future__ import annotations

from typing import Optional

from action_executor import ActionExecutor, ActionResult


class CompositeExecutor(ActionExecutor):
    name = "macos-composite"

    def __init__(self, primary: ActionExecutor, fallback: ActionExecutor):
        self._primary = primary
        self._fallback = fallback
        self.name = f"composite({primary.name}->{fallback.name})"

    async def _with_fallback(self, method: str, *args, **kwargs) -> ActionResult:
        result = await getattr(self._primary, method)(*args, **kwargs)
        if result.supported:
            return result  # handled (success OR a real failure) — do not retry
        alt = await getattr(self._fallback, method)(*args, **kwargs)
        # If neither backend supports it, surface the primary's reason (the
        # AppleScript-only floor) which is the more useful message.
        return alt if alt.supported else result

    # --- Apps / input -----------------------------------------------------

    async def open_app(self, app: str, *, task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("open_app", app, task_id=task_id)

    async def open_path(self, path: str, *, task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("open_path", path, task_id=task_id)

    async def run_app_command(self, app: str, command: str, *, task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("run_app_command", app, command, task_id=task_id)

    async def send_keystroke(self, app: str, text: str, *, press_enter: bool = False,
                             task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("send_keystroke", app, text,
                                         press_enter=press_enter, task_id=task_id)

    # --- Files / browser / scripts (AppleScript owns these; AX returns
    #     not_supported, so these stay on the primary) ---------------------

    async def read_file(self, path: str) -> ActionResult:
        return await self._with_fallback("read_file", path)

    async def write_file(self, path: str, content: str) -> ActionResult:
        return await self._with_fallback("write_file", path, content)

    async def move_file(self, src: str, dst: str) -> ActionResult:
        return await self._with_fallback("move_file", src, dst)

    async def delete_file(self, path: str) -> ActionResult:
        return await self._with_fallback("delete_file", path)

    async def list_folder(self, path: str) -> ActionResult:
        return await self._with_fallback("list_folder", path)

    async def navigate(self, url: str, *, browser: str = "chrome") -> ActionResult:
        return await self._with_fallback("navigate", url, browser=browser)

    async def run_script(self, script: str) -> ActionResult:
        return await self._with_fallback("run_script", script)

    async def is_app_scriptable(self, app: str) -> bool:
        return await self._primary.is_app_scriptable(app)

    # --- universal control (UC1) — AppleScript can't do these, so they fall
    #     through to the Accessibility backend ------------------------------
    async def observe_ui(self, *, app: Optional[str] = None, max_elements: int = 250,
                         task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("observe_ui", app=app,
                                         max_elements=max_elements, task_id=task_id)

    async def click_element(self, *, ref: Optional[str] = None, point: Optional[tuple] = None,
                           app: Optional[str] = None, task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("click_element", ref=ref, point=point,
                                         app=app, task_id=task_id)

    async def key_combo(self, combo: str, *, app: Optional[str] = None,
                       task_id: Optional[str] = None) -> ActionResult:
        return await self._with_fallback("key_combo", combo, app=app, task_id=task_id)
