"""
VALET Process Event Bus — broadcasts structured events about what VALET
is actively doing to subscribed WebSocket clients.

The frontend uses these events to render a live "process panel" that shows
browse actions, code-task output, app launches, screenshots, errors, etc.
Action handlers wrap their work in `bus.task_context("Browsing for X")`
and emit step / browser_action / screenshot / etc. events as they happen.

All events flow through a single module-level `bus` singleton.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("valet.process_events")


class EventType(str, Enum):
    # Lifecycle — emitted automatically by task_context.
    TASK_START = "task_start"
    TASK_DONE = "task_done"

    # Content events — emitted by action handlers.
    STEP = "step"
    SCREENSHOT = "screenshot"
    BROWSER_ACTION = "browser_action"
    APP_LAUNCH = "app_launch"
    TEXT_WRITE = "text_write"
    CODE_TASK = "code_task"
    TASK_QUEUED = "task_queued"
    POINTER_HIGHLIGHT = "pointer_highlight"
    CURSOR_CONTROL = "cursor_control"
    ERROR = "error"


class EventStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    ERROR = "error"


@dataclass
class Event:
    """A single process-panel event broadcast over WebSocket as JSON."""
    type: str
    title: str
    task_id: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    status: str = "active"
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ProcessEventBus:
    """Async pub/sub for process events.

    Subscribers are objects with `await send_json(dict)` (FastAPI WebSocket).
    Dead subscribers are pruned on the next emit so callers never see send
    failures.
    """

    def __init__(self) -> None:
        self._subs: list[Any] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, ws) -> None:
        async with self._lock:
            if ws not in self._subs:
                self._subs.append(ws)

    async def unsubscribe(self, ws) -> None:
        async with self._lock:
            if ws in self._subs:
                self._subs.remove(ws)

    async def broadcast(self, message: dict) -> None:
        """Send an arbitrary message dict to all subscribers.

        Reuses the connected-WS set (and dead-sub pruning) for non-event control
        frames — e.g. global push-to-talk state ({"type": "ptt", ...}). The bus
        already tracks exactly the live voice WebSockets, so control frames that
        originate off the request path (a background event tap) ride the same
        fan-out instead of duplicating the connection registry."""
        async with self._lock:
            subs = list(self._subs)
        dead: list[Any] = []
        for ws in subs:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._subs:
                        self._subs.remove(ws)

    async def emit(self, event: Event) -> None:
        """Broadcast an event to all subscribers."""
        await self.broadcast({"type": "process_event", "event": event.to_dict()})

    @asynccontextmanager
    async def task_context(self, title: str, detail: str = ""):
        """Auto-emit task_start on enter, task_done (or error) on exit.

        Yields the task_id so the caller can emit child events tied to it:

            async with bus.task_context("Researching X") as task_id:
                await emit_step(task_id, "Searching...")
        """
        task_id = str(uuid.uuid4())[:8]
        await self.emit(Event(
            type=EventType.TASK_START.value,
            task_id=task_id,
            title=title,
            detail=detail,
            status=EventStatus.ACTIVE.value,
        ))
        try:
            yield task_id
        except asyncio.CancelledError:
            # User-initiated cancel (e.g. "stop" / "cancel" during research) —
            # not an error. Still need to emit task_done so the frontend's
            # active-task counter unwinds and auto-dismiss can fire.
            await self.emit(Event(
                type=EventType.TASK_DONE.value,
                task_id=task_id,
                title=title,
                detail="cancelled",
                status=EventStatus.DONE.value,
            ))
            raise
        except Exception as e:
            await self.emit(Event(
                type=EventType.ERROR.value,
                task_id=task_id,
                title=f"Error: {title}",
                detail=str(e)[:500],
                status=EventStatus.ERROR.value,
            ))
            await self.emit(Event(
                type=EventType.TASK_DONE.value,
                task_id=task_id,
                title=title,
                status=EventStatus.ERROR.value,
            ))
            raise
        else:
            await self.emit(Event(
                type=EventType.TASK_DONE.value,
                task_id=task_id,
                title=title,
                status=EventStatus.DONE.value,
            ))


# Module-level singleton. Every action handler imports this.
bus = ProcessEventBus()


# ---------------------------------------------------------------------------
# Convenience helpers — keep action code concise.
# ---------------------------------------------------------------------------

async def emit_step(task_id: str, title: str, detail: str = "",
                    status: str = "active",
                    payload: dict[str, Any] | None = None) -> None:
    await bus.emit(Event(
        type=EventType.STEP.value,
        task_id=task_id,
        title=title,
        detail=detail,
        status=status,
        payload=payload or {},
    ))


async def emit_browser_action(task_id: str, action: str, url: str = "",
                              detail: str = "") -> None:
    await bus.emit(Event(
        type=EventType.BROWSER_ACTION.value,
        task_id=task_id,
        title=action,
        detail=detail,
        payload={"url": url},
    ))


async def emit_pointer(task_id: str, x: float, y: float, label: str = "",
                       frame: list | None = None) -> None:
    """Point-and-teach: tell the frontend where the located element sits on the
    real screen. `x,y` is the absolute screen center; `frame` is the element's
    [x,y,w,h] global rect when known (AX path), else None (vision path — the
    panel falls back to a fixed reticle). No synthetic input is ever sent — this
    only renders an in-app confirmation of what VALET is describing aloud."""
    await bus.emit(Event(
        type=EventType.POINTER_HIGHLIGHT.value,
        task_id=task_id,
        title=label or "Here",
        payload={"x": x, "y": y, "frame": frame, "label": label},
    ))


async def emit_cursor_control(task_id: str, active: bool, label: str = "") -> None:
    """Signal that Vee is (active) or is no longer (not active) steering the real
    macOS cursor, so the frontend can show a prominent takeover indicator while a
    glide-and-click is happening."""
    await bus.emit(Event(
        type=EventType.CURSOR_CONTROL.value,
        task_id=task_id,
        title=f"Steering cursor{f' → {label}' if label else ''}" if active else "Released cursor",
        status="active" if active else "done",
        payload={"active": active, "label": label},
    ))


async def emit_screenshot(task_id: str, path: str, caption: str = "") -> None:
    await bus.emit(Event(
        type=EventType.SCREENSHOT.value,
        task_id=task_id,
        title=caption or "Screenshot",
        payload={"path": path},
    ))


async def emit_app_launch(task_id: str, app_name: str, status: str = "done",
                          detail: str = "") -> None:
    await bus.emit(Event(
        type=EventType.APP_LAUNCH.value,
        task_id=task_id,
        title=app_name,
        detail=detail,
        status=status,
        payload={"app": app_name},
    ))


async def emit_text_write(task_id: str, app: str, text: str,
                          sent: bool = False) -> None:
    await bus.emit(Event(
        type=EventType.TEXT_WRITE.value,
        task_id=task_id,
        title=f"{'Sent' if sent else 'Typed'} in {app or 'active app'}",
        detail=text[:500],
        payload={"app": app, "sent": sent, "text": text},
    ))


async def emit_code_task(task_id: str, line: str, status: str = "active") -> None:
    await bus.emit(Event(
        type=EventType.CODE_TASK.value,
        task_id=task_id,
        title="claude code",
        detail=line,
        status=status,
    ))


async def emit_error(task_id: str, title: str, detail: str = "") -> None:
    await bus.emit(Event(
        type=EventType.ERROR.value,
        task_id=task_id,
        title=title,
        detail=detail,
        status=EventStatus.ERROR.value,
    ))


async def emit_task_queued(task_id: str, title: str, detail: str = "") -> None:
    await bus.emit(Event(
        type=EventType.TASK_QUEUED.value,
        task_id=task_id,
        title=title,
        detail=detail,
        status=EventStatus.PENDING.value,
    ))


async def emit_voice_timing(summary: str, segments_ms: dict[str, float],
                            total_ms: float | None) -> None:
    """Opt-in: surface a voice-turn latency summary as a one-shot panel row.

    Reuses the generic STEP event so the existing Process Panel renderer
    handles it with no frontend change. Carries only timing numbers (ms) and
    mark names — never transcript content. Gated by the caller (VALET only
    emits this when VALET_VOICE_TIMING is set), so default UX is unchanged.
    """
    await bus.emit(Event(
        type=EventType.STEP.value,
        task_id=str(uuid.uuid4())[:8],
        title="voice latency",
        detail=summary,
        status=EventStatus.DONE.value,
        payload={"segments_ms": segments_ms, "total_ms": total_ms},
    ))


async def emit_project_event(task_id: str, stage: str, title: str,
                             detail: str = "", status: str = "active",
                             payload: dict[str, Any] | None = None) -> None:
    """Emit a project-lifecycle event (creating / scaffolding / opening_* / ready).

    The event `type` is `project.<stage>` so the frontend can target stages
    individually if it wants distinct icons, while falling back to generic
    step rendering for stages it doesn't know yet.
    """
    await bus.emit(Event(
        type=f"project.{stage}",
        task_id=task_id,
        title=title,
        detail=detail,
        status=status,
        payload=payload or {},
    ))


async def emit_context_event(task_id: str, stage: str, title: str,
                             detail: str = "", status: str = "active",
                             payload: dict[str, Any] | None = None) -> None:
    """Emit a warm-context-loader event (loading / file_read / ready / refreshed).

    Symmetric with `emit_project_event` — the event `type` is `context.<stage>`.
    """
    await bus.emit(Event(
        type=f"context.{stage}",
        task_id=task_id,
        title=title,
        detail=detail,
        status=status,
        payload=payload or {},
    ))


async def emit_plan_stage(task_id: str, index: int, total: int, title: str,
                          status: str = "pending", plan_title: str = "",
                          payload: dict[str, Any] | None = None) -> None:
    """Emit one stage of a live-tracked plan for the Process Panel.

    All stages of a plan share the event `task_id`; the frontend groups them
    into a single branching plan block keyed by that id, and keys each node by
    `payload.index` so a re-emitted stage (status pending → active → done)
    updates its node in place rather than appending a new row.
    """
    p = {"index": index, "total": total, "plan_title": plan_title}
    if payload:
        p.update(payload)
    await bus.emit(Event(
        type="plan.stage",
        task_id=task_id,
        title=title,
        status=status,
        payload=p,
    ))


async def emit_tool_event(task_id: str, event_type: str, title: str,
                          detail: str = "", status: str = "done",
                          payload: dict[str, Any] | None = None) -> None:
    """Emit a structured Claude Code tool-call event for the Process Panel.

    `event_type` is the full type string (e.g. "tool.file_read", "tool.bash",
    "tool.web_search", "tool.thinking", "tool.result"). The Process Panel
    renders each as a discrete row with an icon + title + truncated detail.
    """
    await bus.emit(Event(
        type=event_type,
        task_id=task_id,
        title=title,
        detail=detail,
        status=status,
        payload=payload or {},
    ))
