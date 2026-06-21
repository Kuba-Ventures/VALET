"""Global push-to-talk — system-wide hold-⌥ detection (Clicky-style).

A Quartz ``CGEventTap`` running on a dedicated daemon thread watches the Option
(⌥) modifier flag across EVERY app, so the user can hold Option in *any* window —
not just VALET's — to talk. On each down/up transition it calls
``on_change("down"|"up")``, which the server bridges to a WebSocket frame the
frontend treats exactly like the in-window ⌥ key (``wake.beginPushToTalk`` /
``endPushToTalk``).

Why a CGEventTap and not an NSEvent global monitor: the backend runs an asyncio
event loop, not a Cocoa run loop, so ``NSEvent`` global monitors would never
fire here. A CGEventTap runs on its own ``CFRunLoop`` on a thread we own. The tap
is **listen-only**, so it never swallows the key — Option keeps working normally
everywhere (Option+letter, menu shortcuts, etc.).

Permission: listening to key/flag events requires macOS **Input Monitoring**
(System Settings → Privacy & Security → Input Monitoring) for the host process
(Terminal/Python in dev, the VALET app once shipped). Without it,
``CGEventTapCreate`` returns NULL and this module quietly no-ops — global PTT
just stays off and the in-window ⌥ still works. Accessibility (already granted
for cursor control) covers POSTING events, not LISTENING, so this is a distinct,
one-time grant.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger("valet.ptt")

try:
    import Quartz  # PyObjC
    _AVAILABLE = True
except Exception:  # pragma: no cover - non-mac / no PyObjC
    Quartz = None  # type: ignore
    _AVAILABLE = False


class GlobalPTT:
    """System-wide Option-hold detector. Construct with a thread-safe callback.

    ``on_change`` is invoked from the event-tap thread with "down" or "up" on
    each Option transition — keep it non-blocking (the server hops back onto the
    asyncio loop via ``run_coroutine_threadsafe``).
    """

    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._on_change = on_change
        self._thread: Optional[threading.Thread] = None
        self._tap = None
        self._option_down = False
        self._started = False

    @property
    def available(self) -> bool:
        return _AVAILABLE

    def start(self) -> bool:
        """Spin up the tap thread. Returns True if the thread was launched.

        A True here means the thread started, not that the tap was permitted —
        the permission check happens inside ``_run`` (CGEventTapCreate), which
        logs and exits cleanly if Input Monitoring isn't granted yet."""
        if not _AVAILABLE or self._started:
            return False
        self._started = True
        self._thread = threading.Thread(
            target=self._run, name="valet-global-ptt", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,            # tap at the HID layer (all apps)
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,  # observe only — never swallow keys
            mask,
            self._callback,
            None,
        )
        if not tap:
            log.warning(
                "global PTT off: CGEventTapCreate returned NULL — grant VALET "
                "(or Terminal/Python in dev) Input Monitoring in System Settings "
                "to hold ⌥ in any app. The in-window ⌥ still works without it.")
            self._started = False
            return
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        log.info("global PTT active — hold ⌥ in any app to talk")
        Quartz.CFRunLoopRun()

    def _callback(self, proxy, etype, event, refcon):  # noqa: ANN001 - Quartz ABI
        try:
            # macOS disables a tap that times out or is disabled by user input;
            # re-enable so PTT keeps working for the whole session.
            if etype in (Quartz.kCGEventTapDisabledByTimeout,
                         Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event
            if etype == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                down = bool(flags & Quartz.kCGEventFlagMaskAlternate)
                # Fire only on a real Option transition — flagsChanged also fires
                # when OTHER modifiers change while Option is held; those keep the
                # Alternate bit steady and must not re-trigger.
                if down != self._option_down:
                    self._option_down = down
                    try:
                        self._on_change("down" if down else "up")
                    except Exception:
                        log.exception("global PTT on_change failed")
        except Exception:
            log.exception("global PTT callback error")
        return event  # listen-only: pass the event through untouched
