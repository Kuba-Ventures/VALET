"""Global push-to-talk — system-wide hold-⌃⌥ chord detection (Clicky-style).

A Quartz ``CGEventTap`` running on a dedicated daemon thread watches the Control
(⌃) and Option (⌥) modifier flags across EVERY app, so the user can hold the
**⌃⌥ chord** in *any* window — not just VALET's — to talk. On each chord
transition it calls ``on_change("down"|"up"|"cancel")``, which the server bridges
to a WebSocket frame the frontend treats exactly like the in-window PTT key
(``wake.beginPushToTalk`` / ``endPushToTalk`` / ``cancelPushToTalk``).

The chord is the menu-bar product's single global front door. ⌃⌥ together is
rarely held on its own, so it makes a clean push-to-talk trigger; bare ⌥ (the
old trigger) collided with Option-typing. To stay out of the way of real
⌃⌥-letter shortcuts, the tap ALSO watches key-downs: if any non-modifier key is
pressed while the chord is held, the turn is **cancelled** ("cancel") and the
captured audio discarded, so e.g. ⌃⌥-arrow / ⌃⌥-letter app shortcuts still work.

Why a CGEventTap and not an NSEvent global monitor: the backend runs an asyncio
event loop, not a Cocoa run loop, so ``NSEvent`` global monitors would never
fire here. A CGEventTap runs on its own ``CFRunLoop`` on a thread we own. The tap
is **listen-only**, so it never swallows keys — ⌃ and ⌥ keep working normally
everywhere (modifier-letter combos, menu shortcuts, etc.).

Permission: listening to key/flag events requires macOS **Input Monitoring**
(System Settings → Privacy & Security → Input Monitoring) for the host process
(Terminal/Python in dev, the VALET app once shipped). Without it,
``CGEventTapCreate`` returns NULL and this module quietly no-ops — global PTT
just stays off and the in-window key still works. Accessibility (already granted
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
    """System-wide ⌃⌥-chord-hold detector. Construct with a thread-safe callback.

    ``on_change`` is invoked from the event-tap thread with "down" / "up" /
    "cancel" on each chord transition — keep it non-blocking (the server hops
    back onto the asyncio loop via ``run_coroutine_threadsafe``).

    The chord state machine lives in the pure ``_on_flags`` / ``_on_keydown``
    helpers (no Quartz), so it is unit-testable without an event tap.
    """

    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._on_change = on_change
        self._thread: Optional[threading.Thread] = None
        self._tap = None
        # Chord state: ``_active`` is True while ⌃⌥ are both held and a turn is
        # live; ``_cancelled`` latches if a non-modifier key lands mid-hold so the
        # release does NOT dispatch (it was a real ⌃⌥-letter shortcut).
        self._active = False
        self._cancelled = False
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

    # --- pure chord state machine (no Quartz; unit-testable) ----------------

    def _on_flags(self, control: bool, option: bool) -> None:
        """Handle a modifier-flags change. Emits "down" when the ⌃⌥ chord
        engages and "up" when it disengages (unless the turn was cancelled)."""
        chord = control and option
        if chord and not self._active:
            self._active = True
            self._cancelled = False
            self._emit("down")
        elif not chord and self._active:
            was_cancelled = self._cancelled
            self._active = False
            self._cancelled = False
            if not was_cancelled:
                self._emit("up")

    def _on_keydown(self) -> None:
        """Handle a non-modifier key-down. While the chord is held this means a
        real ⌃⌥-letter shortcut — cancel the turn and discard the mic once."""
        if self._active and not self._cancelled:
            self._cancelled = True
            self._emit("cancel")

    def _emit(self, state: str) -> None:
        try:
            self._on_change(state)
        except Exception:
            log.exception("global PTT on_change failed")

    # --- Quartz event tap ----------------------------------------------------

    def _run(self) -> None:
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown))
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
                "to hold ⌃⌥ in any app. The in-window PTT key still works without it.")
            self._started = False
            return
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        log.info("global PTT active — hold ⌃⌥ in any app to talk")
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
                control = bool(flags & Quartz.kCGEventFlagMaskControl)
                option = bool(flags & Quartz.kCGEventFlagMaskAlternate)
                # _on_flags only fires on a real chord transition; flagsChanged
                # also fires when OTHER modifiers move while ⌃⌥ stay held — those
                # keep `chord` steady, so nothing re-triggers.
                self._on_flags(control, option)
            elif etype == Quartz.kCGEventKeyDown:
                # A non-modifier key while the chord is held → a real ⌃⌥-shortcut;
                # cancel the turn so we don't dispatch the user's keystroke as voice.
                self._on_keydown()
        except Exception:
            log.exception("global PTT callback error")
        return event  # listen-only: pass the event through untouched
