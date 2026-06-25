"""macOS Accessibility / synthetic-input backend (Phase K — UC1).

The AppleScript backend can only drive apps that ship a scripting dictionary.
This backend reaches the rest through the macOS Accessibility (AX) APIs and
Quartz synthetic events — the universal-control primitives the rest of the track
builds on:

  * observe_ui   — enumerate the focused window's AX tree into a structured,
                   ref-addressable element list (role/title/value/enabled/frame).
  * click_element— click an element by `ref` (AXPress where available, else a
                   synthetic CGEvent mouse click at its centre) or by `point`.
  * key_combo    — post a modifier chord (e.g. "cmd+s") as CGEvents.
  * send_keystroke / open_app — type text / activate an app (carried from K1).

It is the first fallback layer under the portable `ActionExecutor` interface;
`CompositeExecutor` routes a capability the AppleScript primary reports
`not_supported` here. File ops / navigate / run_script stay not_supported (the
composite keeps those on AppleScript).

Requires pyobjc (ApplicationServices + Quartz + AppKit) and the macOS
**Accessibility** permission (`AXIsProcessTrusted`). With pyobjc absent every
method returns a clean `not_supported` — it never raises, and importing this
module never fails. When pyobjc is present but Accessibility is not granted, AX
calls fail cleanly and the methods return a failure that names the permission.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from action_executor import ActionExecutor, ActionResult, Capability, UIElement

log = logging.getLogger("valet.accessibility")

# Guarded optional dependency. If the pyobjc frameworks aren't present (e.g. a
# dev box that only installed the EventKit/Contacts wrappers), the backend is
# inert rather than broken.
try:
    import Quartz  # type: ignore
    import ApplicationServices as _AX  # type: ignore
    from AppKit import NSWorkspace, NSRunningApplication  # type: ignore

    _PYOBJC = True
except Exception:  # ImportError, or a non-macOS host
    _PYOBJC = False

_RETURN_KEYCODE = 36  # virtual keycode for Return

# AX attribute / action names. Plain strings — robust across pyobjc versions and
# exactly what the AX API expects.
_kChildren = "AXChildren"
_kRole = "AXRole"
_kSubrole = "AXSubrole"
_kTitle = "AXTitle"
_kValue = "AXValue"
_kDesc = "AXDescription"
_kRoleDesc = "AXRoleDescription"
_kPosition = "AXPosition"
_kSize = "AXSize"
_kEnabled = "AXEnabled"
_kFocusedWindow = "AXFocusedWindow"
_kMainWindow = "AXMainWindow"
_kWindows = "AXWindows"
_kMenuBar = "AXMenuBar"
_kPressAction = "AXPress"

# How deep / wide we walk an AX tree. Bounds latency and token cost downstream.
_MAX_DEPTH = 20
# Web pages (esp. GitHub) expose hundreds of interactive elements once the full
# a11y tree is built; keep the cap high enough that nav/toolbar controls aren't
# truncated before the resolver ever sees them (the resolver shows the model the
# first ~200 of these).
_MAX_ELEMENTS = 250


# --------------------------------------------------------------------------- #
# Trust (Accessibility permission)
# --------------------------------------------------------------------------- #
def is_trusted() -> bool:
    """Real TCC check: is THIS process trusted for Accessibility? No prompt."""
    if not _PYOBJC:
        return False
    try:
        return bool(_AX.AXIsProcessTrusted())
    except Exception:
        return False


def is_trusted_prompt() -> bool:
    """Like `is_trusted`, but ask macOS to show the 'grant Accessibility' prompt
    if not yet trusted. Returns the *current* trust state (the grant itself
    happens in System Settings and usually needs an app restart to take hold)."""
    if not _PYOBJC:
        return False
    try:
        opts = {_AX.kAXTrustedCheckOptionPrompt: True}
        return bool(_AX.AXIsProcessTrustedWithOptions(opts))
    except Exception:
        return is_trusted()


# --------------------------------------------------------------------------- #
# pyobjc / AX helpers (all synchronous — call via asyncio.to_thread)
# --------------------------------------------------------------------------- #
def _activate_app(app: str) -> bool:
    """Bring `app` to the front so synthetic events land in it. Best-effort.

    Skips activation when `app` is ALREADY frontmost: re-activating fires a focus
    event that dismisses transient popovers/menus (e.g. Google's account menu),
    which made multi-step menu flows oscillate — click avatar → menu opens → next
    click re-activates → menu closes → click lands on the wrong thing."""
    try:
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front and (front.localizedName() or "").strip().lower() == app.strip().lower():
            return True  # already frontmost — don't re-activate and kill the popup
        for running in ws.runningApplications():
            name = running.localizedName() or ""
            if name.lower() == app.lower():
                running.activateWithOptions_(1 << 1)  # ActivateIgnoringOtherApps
                return True
        return bool(ws.launchApplication_(app))
    except Exception as e:
        log.warning("activate %s failed: %s", app, e)
        return False


def _is_valet(name: Optional[str]) -> bool:
    """Our own app/windows — never the target of observation/control (Phase 2)."""
    return "valet" in (name or "").lower()


def _app_name_for_pid(pid: int) -> Optional[str]:
    """Localized app name for a pid (so observe_ui reports the REAL app, not
    'frontmost') — used to activate the right app before typing into it."""
    try:
        a = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return (a.localizedName() if a else None) or None
    except Exception:
        return None


def _topmost_non_valet_pid(ws) -> Optional[int]:
    """Owner pid of the topmost on-screen window that isn't VALET's — the app the
    user was looking at *behind* Vee (Vee is frontmost while they talk to it)."""
    try:
        valet = set()
        for r in ws.runningApplications():
            if _is_valet(r.localizedName()):
                valet.add(int(r.processIdentifier()))
        opts = (Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements)
        info = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
    except Exception:
        return None
    for w in info or []:
        if int(w.get(Quartz.kCGWindowLayer, 0)) != 0:
            continue
        pid = w.get(Quartz.kCGWindowOwnerPID)
        if pid is None or int(pid) in valet or _is_valet(w.get(Quartz.kCGWindowOwnerName, "")):
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        if b.get("Width", 0) < 200 or b.get("Height", 0) < 150:
            continue
        return int(pid)
    return None


def _pid_for_app(app: Optional[str]) -> Optional[int]:
    """App name → running pid. None → the FRONTMOST NON-VALET app (Phase 2): skip
    our own windows and target the app stacked just behind Vee."""
    try:
        ws = NSWorkspace.sharedWorkspace()
        if app:
            for running in ws.runningApplications():
                if (running.localizedName() or "").lower() == app.lower():
                    return int(running.processIdentifier())
            return None
        front = ws.frontmostApplication()
        if front and not _is_valet(front.localizedName()):
            return int(front.processIdentifier())
        return _topmost_non_valet_pid(ws) or (int(front.processIdentifier()) if front else None)
    except Exception as e:
        log.warning("pid lookup for %r failed: %s", app, e)
    return None


def _copy_attr(element, attr: str):
    """AXUIElementCopyAttributeValue → value or None (never raises)."""
    try:
        err, val = _AX.AXUIElementCopyAttributeValue(element, attr, None)
        if err == 0:
            return val
    except Exception:
        pass
    return None


def _frame_of(element) -> Optional[list]:
    """[x, y, w, h] in global screen points, or None."""
    pos = _copy_attr(element, _kPosition)
    size = _copy_attr(element, _kSize)
    if pos is None or size is None:
        return None
    try:
        ok_p, pt = _AX.AXValueGetValue(pos, _AX.kAXValueCGPointType, None)
        ok_s, sz = _AX.AXValueGetValue(size, _AX.kAXValueCGSizeType, None)
        if ok_p and ok_s:
            return [float(pt.x), float(pt.y), float(sz.width), float(sz.height)]
    except Exception:
        pass
    return None


def _str_attr(element, attr: str) -> str:
    v = _copy_attr(element, attr)
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _focused_window(app_el):
    """The app's focused window AX element (fallbacks: main window, first window)."""
    win = _copy_attr(app_el, _kFocusedWindow) or _copy_attr(app_el, _kMainWindow)
    if win is not None:
        return win
    wins = _copy_attr(app_el, _kWindows)
    if wins:
        try:
            return wins[0]
        except Exception:
            return None
    return None


def _is_interesting(role: str, title: str, value: str) -> bool:
    """Keep elements a user could plausibly target. Containers with no label and
    no value are dropped from the flat list (we still recurse into them)."""
    if role in (
        "AXButton", "AXMenuItem", "AXMenuButton", "AXCheckBox", "AXRadioButton",
        "AXTextField", "AXTextArea", "AXSearchField", "AXPopUpButton",
        "AXComboBox", "AXLink", "AXTab", "AXSlider", "AXDisclosureTriangle",
        "AXSegmentedControl", "AXIncrementor", "AXStepper",
    ):
        return True
    return bool(title or value)


def _enumerate_window(win_el, max_elements: int):
    """Walk the window AX subtree breadth-first; return (elements, ref_map).

    elements: list[UIElement]; ref_map: {ref -> AXUIElement} for click resolution.
    """
    elements: list[UIElement] = []
    ref_map: dict = {}
    queue = [(win_el, 0)]
    idx = 0
    seen = 0
    web_top = None  # top-y where web CONTENT starts (below the browser's toolbar)
    while queue and len(elements) < max_elements:
        el, depth = queue.pop(0)
        seen += 1
        if seen > max_elements * 8:  # hard walk cap, independent of kept count
            break
        role = _str_attr(el, _kRole)
        title = _str_attr(el, _kTitle) or _str_attr(el, _kDesc)
        value = _str_attr(el, _kValue)
        # Record where the rendered page begins, so callers can tell page content
        # apart from the browser's own chrome (toolbar/profile/tabs/address bar).
        if role == "AXWebArea":
            _wf = _frame_of(el)
            if _wf and (web_top is None or _wf[1] < web_top):
                web_top = _wf[1]
        if _is_interesting(role, title, value):
            ref = f"e{idx}"
            idx += 1
            enabled_v = _copy_attr(el, _kEnabled)
            elements.append(UIElement(
                ref=ref,
                role=role or "AXUnknown",
                title=title[:200],
                value=value[:200],
                enabled=bool(enabled_v) if enabled_v is not None else True,
                frame=_frame_of(el),
            ))
            ref_map[ref] = el
        if depth < _MAX_DEPTH:
            kids = _copy_attr(el, _kChildren)
            if kids:
                for k in kids:
                    queue.append((k, depth + 1))
    return elements, ref_map, web_top


def _enumerate_menu_bar(app_el, start_idx: int):
    """Top-level menu-bar items (Apple menu, the app menu, File, Edit, View, …) for
    the app, as (elements, ref_map). The menu bar is NOT inside any window subtree,
    so the window walk misses it entirely — without this, "point at the Apple menu"
    or "open the View menu" can never resolve, which is exactly why menu-bar
    walkthrough steps failed. Shallow by design: only the bar items themselves
    (descending into a menu requires pressing it open first). Refs are prefixed `m`
    so they never collide with the window walk's `e` refs."""
    elements: list[UIElement] = []
    ref_map: dict = {}
    bar = _copy_attr(app_el, _kMenuBar)
    if bar is None:
        return elements, ref_map
    items = _copy_attr(bar, _kChildren) or []
    idx = start_idx
    for i, it in enumerate(items):
        role = _str_attr(it, _kRole) or "AXMenuBarItem"
        title = _str_attr(it, _kTitle) or _str_attr(it, _kDesc)
        # The first bar item is the Apple menu; its AXTitle is usually "Apple" or
        # empty — name it the way the resolver and the user both refer to it.
        if i == 0 and (not title or title.strip().lower() == "apple"):
            title = "Apple menu"
        frame = _frame_of(it)
        if not frame:
            continue  # off-screen / no geometry → can't point at it honestly
        ref = f"m{idx}"
        idx += 1
        enabled_v = _copy_attr(it, _kEnabled)
        elements.append(UIElement(
            ref=ref,
            role=role,
            title=title[:200],
            value="",
            enabled=bool(enabled_v) if enabled_v is not None else True,
            frame=frame,
        ))
        ref_map[ref] = it
    return elements, ref_map


def _action_names(element) -> list:
    try:
        err, names = _AX.AXUIElementCopyActionNames(element, None)
        if err == 0 and names:
            return list(names)
    except Exception:
        pass
    return []


def _ax_press(element) -> bool:
    try:
        return _AX.AXUIElementPerformAction(element, _kPressAction) == 0
    except Exception:
        return False


def _mouse_click(x: float, y: float) -> None:
    """Synthetic left click at a global screen point.

    Posts a move first (so the target registers hover), then a down/up carrying
    click-state, with brief gaps between events. An instantaneous, stateless
    down/up — the naive version — is silently dropped by some targets, notably
    browser web content (a Gmail row would be hovered but never opened)."""
    pt = Quartz.CGPointMake(x, y)
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    move = Quartz.CGEventCreateMouseEvent(
        src, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft)
    down = Quartz.CGEventCreateMouseEvent(
        src, Quartz.kCGEventLeftMouseDown, pt, Quartz.kCGMouseButtonLeft)
    up = Quartz.CGEventCreateMouseEvent(
        src, Quartz.kCGEventLeftMouseUp, pt, Quartz.kCGMouseButtonLeft)
    # click count = 1 so the event reads as a real single click, not a bare press.
    Quartz.CGEventSetIntegerValueField(down, Quartz.kCGMouseEventClickState, 1)
    Quartz.CGEventSetIntegerValueField(up, Quartz.kCGMouseEventClickState, 1)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    time.sleep(0.02)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(0.03)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


# --------------------------------------------------------------------------- #
# Stage 2 — visible cursor glide (point-and-teach's act sibling).
#
# CGEvent mouse coords are GLOBAL DISPLAY POINTS (top-left origin) — the same
# space as UIElement.frame — so no Retina/pixel conversion and multi-monitor
# works for free. The glide is SYNCHRONOUS and meant to run on a worker thread
# (see AccessibilityExecutor.glide_to_target) so a busy asyncio loop can't jank
# the motion. clock/sleep/read_pos/post_move are injectable so the tween is
# unit-tested deterministically with no real mouse.
# --------------------------------------------------------------------------- #
def _cursor_location() -> tuple:
    """Current global cursor position in points."""
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (float(loc.x), float(loc.y))


def _post_mouse_moved(x: float, y: float) -> None:
    pt = Quartz.CGPointMake(x, y)
    ev = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def move_cursor(x: float, y: float, *, duration: float = 0.45, fps: int = 60,
                divergence: float = 8.0, clock=time.perf_counter, sleep=time.sleep,
                read_pos=_cursor_location, post_move=_post_mouse_moved) -> dict:
    """Glide the real cursor to (x, y) over `duration`, eased (smoothstep). Aborts
    if the user grabs the physical mouse (actual position diverges from the point
    we last commanded) or the per-move budget is blown.

    Returns {'arrived': bool, 'aborted': None|'user_moved'|'timeout', 'pos': (x,y)}.
    """
    sx, sy = read_pos()
    steps = max(1, int(duration * fps))
    frame = duration / steps
    last_cmd = (sx, sy)
    deadline = clock() + duration + 0.5
    for i in range(1, steps + 1):
        # Physical-mouse grab: the cursor isn't where we last put it → yield.
        ax, ay = read_pos()
        if abs(ax - last_cmd[0]) > divergence or abs(ay - last_cmd[1]) > divergence:
            return {"arrived": False, "aborted": "user_moved", "pos": (ax, ay)}
        if clock() > deadline:
            return {"arrived": False, "aborted": "timeout", "pos": (ax, ay)}
        t = i / steps
        e = t * t * (3.0 - 2.0 * t)                  # smoothstep easing
        cx = sx + (x - sx) * e
        cy = sy + (y - sy) * e
        post_move(cx, cy)
        last_cmd = (cx, cy)
        sleep(frame)
    return {"arrived": True, "aborted": None, "pos": (float(x), float(y))}


def _element_at(x: float, y: float):
    """AXUIElement under a global screen point, or None."""
    try:
        sysw = _AX.AXUIElementCreateSystemWide()
        err, el = _AX.AXUIElementCopyElementAtPosition(sysw, float(x), float(y), None)
        if err == 0:
            return el
    except Exception:
        pass
    return None


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


# US-keyboard virtual keycodes for the keys a chord might name. Enough for
# shortcuts; unmapped single chars fall back to a unicode keystroke.
_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


def _parse_combo(combo: str):
    """'cmd+shift+s' -> (flags, keycode | None, literal_char | None)."""
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]
    flags = 0
    key = None
    for p in parts:
        if p in ("cmd", "command", "⌘"):
            flags |= Quartz.kCGEventFlagMaskCommand
        elif p in ("shift", "⇧"):
            flags |= Quartz.kCGEventFlagMaskShift
        elif p in ("opt", "option", "alt", "⌥"):
            flags |= Quartz.kCGEventFlagMaskAlternate
        elif p in ("ctrl", "control", "⌃"):
            flags |= Quartz.kCGEventFlagMaskControl
        elif p in ("fn",):
            flags |= getattr(Quartz, "kCGEventFlagMaskSecondaryFn", 0)
        else:
            key = p  # the non-modifier key (last one wins)
    if key is None:
        return flags, None, None
    if key in _KEYCODES:
        return flags, _KEYCODES[key], None
    if len(key) == 1:
        return flags, None, key  # literal char fallback
    return flags, None, None


def _post_combo(flags: int, keycode, literal) -> bool:
    try:
        if keycode is not None:
            down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
            Quartz.CGEventSetFlags(down, flags)
            up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
            Quartz.CGEventSetFlags(up, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            return True
        if literal is not None:
            down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(down, len(literal), literal)
            Quartz.CGEventSetFlags(down, flags)
            up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(up, len(literal), literal)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            return True
    except Exception as e:
        log.warning("post combo failed: %s", e)
    return False


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
class AccessibilityExecutor(ActionExecutor):
    """Universal-control backend via AX + synthetic CGEvents."""

    name = "macos-accessibility"

    def __init__(self) -> None:
        # ref -> AXUIElement from the most recent observe_ui, so click_element can
        # resolve a ref the model picked. Rebuilt on each observation.
        self._ref_map: dict = {}
        # PIDs we've already asked to expose their full accessibility tree (see
        # observe_ui). Enabling is one-time per app; subsequent observes skip the
        # tree-build delay.
        self._a11y_enabled: set = set()

    # --- trust helpers (no gate) -----------------------------------------
    def is_trusted(self) -> bool:
        return is_trusted()

    def _untrusted(self, capability: Capability) -> ActionResult:
        return ActionResult.failure(
            capability,
            error="accessibility_not_trusted",
            message="I need Accessibility permission to do that, sir — grant it "
                    "in Settings, Permissions, Accessibility.",
        )

    def _unavailable(self, capability: Capability) -> ActionResult:
        return ActionResult.not_supported(
            capability,
            reason="pyobjc (ApplicationServices/Quartz/AppKit) not installed",
            message="I can't reach that app on this Mac yet, sir.",
        )

    # --- observation (Tier 0) --------------------------------------------
    async def observe_ui(self, *, app: Optional[str] = None, max_elements: int = _MAX_ELEMENTS,
                         task_id: Optional[str] = None) -> ActionResult:
        if not _PYOBJC:
            return self._unavailable(Capability.OBSERVE_UI)
        if not is_trusted():
            return self._untrusted(Capability.OBSERVE_UI)

        def _do():
            pid = _pid_for_app(app)
            if pid is None:
                return None, None, "no_target_app", None
            name = _app_name_for_pid(pid) or app or "frontmost"  # the REAL app observed
            app_el = _AX.AXUIElementCreateApplication(pid)
            # Force web/Electron apps (Chrome, Brave, Slack, VS Code, …) to build
            # their FULL accessibility tree. Chromium leaves it thin (just the
            # toolbar) until an assistive tech asks via these attributes, so
            # without this "click <a web thing>" sees no page content and falls
            # back to imprecise vision. With it, real web elements (links,
            # buttons, labelled images) resolve by ref. Idempotent; harmless on
            # apps that don't support it. On first enable per app, give Chromium a
            # beat to build the tree (one-time — repeat observes skip the wait).
            for _attr in ("AXManualAccessibility", "AXEnhancedUserInterface"):
                try:
                    _AX.AXUIElementSetAttributeValue(app_el, _attr, True)
                except Exception:
                    pass
            if pid not in self._a11y_enabled:
                self._a11y_enabled.add(pid)
                # Big pages (GitHub, Gmail) take longer than a fast SPA to build
                # the full tree; 0.35s sometimes observed a half-built tree and
                # missed nav controls. 0.6s is the one-time cost per app.
                time.sleep(0.6)
            win = _focused_window(app_el)
            if win is None:
                return None, name, "no_window", None
            els, ref_map, web_top = _enumerate_window(win, max_elements)
            # Also expose the menu bar (Apple menu + top-level app menus). It lives
            # outside the window tree, so walkthroughs that point at / open a menu
            # ("click the Apple menu") depend on this being in the observation.
            mb_els, mb_map = _enumerate_menu_bar(app_el, len(els))
            els.extend(mb_els)
            ref_map.update(mb_map)
            self._ref_map = ref_map
            return els, name, None, web_top

        els, name, err, web_top = await asyncio.to_thread(_do)
        if err == "no_target_app":
            return ActionResult.failure(Capability.OBSERVE_UI, error=err,
                                        message="I couldn't find that app, sir.")
        if err == "no_window":
            return ActionResult.failure(Capability.OBSERVE_UI, error=err,
                                        message="That app has no window I can read, sir.")
        return ActionResult.success(
            Capability.OBSERVE_UI,
            data={"app": name, "elements": [e.to_dict() for e in els], "web_top": web_top},
            message=f"{len(els)} elements on screen, sir.",
            backend=self.name,
        )

    # --- click (Tier 1) ---------------------------------------------------
    async def click_element(self, *, ref: Optional[str] = None, point: Optional[tuple] = None,
                           app: Optional[str] = None, task_id: Optional[str] = None) -> ActionResult:
        if not _PYOBJC:
            return self._unavailable(Capability.CLICK_ELEMENT)
        if not is_trusted():
            return self._untrusted(Capability.CLICK_ELEMENT)
        if ref is None and point is None:
            return ActionResult.failure(Capability.CLICK_ELEMENT, error="no_target",
                                        message="I need a target to click, sir.")

        def _do():
            # By explicit point — synthetic mouse click. The target app must be
            # frontmost for the click to land there (Phase 2), so activate it.
            if point is not None:
                if app:
                    _activate_app(app)
                    # activateWithOptions_ is async — let the app actually come
                    # frontmost before clicking, or the event lands in whatever
                    # was focused (e.g. Vee's own overlay) instead of the target.
                    time.sleep(0.12)
                _mouse_click(float(point[0]), float(point[1]))
                return True, "point"
            el = self._ref_map.get(ref)
            if el is None:
                return False, "stale_ref"
            # Prefer AXPress: it presses the real control directly, regardless of
            # which app is frontmost — so it works on the app behind Vee with no
            # focus juggling (Phase 2).
            if _kPressAction in _action_names(el) and _ax_press(el):
                return True, "axpress"
            # Mouse fallback needs the window visible/frontmost — activate first.
            if app:
                _activate_app(app)
            frame = _frame_of(el)
            if not frame:
                return False, "no_frame"
            cx, cy = frame[0] + frame[2] / 2.0, frame[1] + frame[3] / 2.0
            _mouse_click(cx, cy)
            return True, "mouse"

        ok, how = await asyncio.to_thread(_do)
        if not ok:
            msg = {
                "stale_ref": "That element is no longer on screen, sir — let me look again.",
                "no_frame": "I can't locate that control on screen, sir.",
            }.get(how, "I couldn't click that, sir.")
            return ActionResult.failure(Capability.CLICK_ELEMENT, error=how, message=msg)
        return ActionResult.success(Capability.CLICK_ELEMENT, message="Clicked, sir.",
                                    backend=self.name, method=how)

    # --- visible cursor glide (Stage 2) ----------------------------------
    def _verify_under(self, ref: str, x: float, y: float) -> bool:
        """True if the AX element under (x, y) still matches the resolved ref
        (role + title/value), so a UI shift during the glide can't cause a wrong
        click. Conservative: any uncertainty (no element, role mismatch) → False."""
        want = self._ref_map.get(ref)
        if want is None:
            return False
        got = _element_at(x, y)
        if got is None:
            return False
        if _str_attr(want, "AXRole") != _str_attr(got, "AXRole"):
            return False
        wt = _str_attr(want, "AXTitle") or _str_attr(want, "AXValue")
        gt = _str_attr(got, "AXTitle") or _str_attr(got, "AXValue")
        if wt and gt and wt != gt:
            return False
        return True

    async def glide_to_target(self, x: float, y: float, *, ref: Optional[str] = None,
                              duration: float = 0.45, verify: bool = True) -> dict:
        """Visibly glide the real cursor to (x, y) on a worker thread (so the
        async voice loop can't jank it), then — when `ref` is given AND `verify`
        — AX hit-test that the element under the cursor still matches. Does NOT
        click; the caller's existing gated click_element fires the actual click,
        so the safety gate (confirm card / kill switch) is preserved.

        `verify=False` skips the post-glide hit-test: browser web content has a
        deep AX tree where the element AT a point is often a child span of the
        resolved row, so the strict role/title match false-aborts ('that moved,
        sir') even though the cursor is dead on the target. The user_moved /
        timeout aborts (physical mouse grab, stall) are always honored.

        Returns {'ok': bool, 'reason': None|'user_moved'|'timeout'|'moved_target'
        |'unavailable'}."""
        if not _PYOBJC or not is_trusted():
            return {"ok": False, "reason": "unavailable"}
        res = await asyncio.to_thread(move_cursor, float(x), float(y), duration=duration)
        if res.get("aborted"):
            return {"ok": False, "reason": res["aborted"]}
        if verify and ref is not None and not await asyncio.to_thread(self._verify_under, ref, x, y):
            return {"ok": False, "reason": "moved_target"}
        return {"ok": True, "reason": None}

    # --- key combo (Tier 1) ----------------------------------------------
    async def key_combo(self, combo: str, *, app: Optional[str] = None,
                       task_id: Optional[str] = None) -> ActionResult:
        if not _PYOBJC:
            return self._unavailable(Capability.KEY_COMBO)
        if not is_trusted():
            return self._untrusted(Capability.KEY_COMBO)

        def _do():
            if app:
                _activate_app(app)
            flags, keycode, literal = _parse_combo(combo)
            if keycode is None and literal is None:
                return False
            return _post_combo(flags, keycode, literal)

        ok = await asyncio.to_thread(_do)
        if ok:
            return ActionResult.success(Capability.KEY_COMBO, message="Done, sir.",
                                        backend=self.name)
        return ActionResult.failure(Capability.KEY_COMBO, error="post_failed",
                                    message=f"I couldn't send {combo}, sir.")

    # --- focus (UC3 helper) ----------------------------------------------
    async def focus_element(self, ref: str) -> bool:
        """Give keyboard focus to an element by ref (AX set-focused) so a typed
        action lands in it. Benign — moves focus, synthesizes no input. Returns
        False if untrusted / unknown ref / unsupported."""
        if not _PYOBJC or not is_trusted():
            return False
        el = self._ref_map.get(ref)
        if el is None:
            return False

        def _do() -> bool:
            try:
                err = _AX.AXUIElementSetAttributeValue(el, "AXFocused", True)
                return err == 0
            except Exception:
                return False

        return await asyncio.to_thread(_do)

    # --- input carried from K1 -------------------------------------------
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
        if not is_trusted():
            return self._untrusted(Capability.SEND_KEYSTROKE)

        def _do() -> bool:
            if app and not _activate_app(app):
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

    # --- capabilities this backend does NOT cover (composite → AppleScript) ---
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
        return {
            Capability.OPEN_APP, Capability.SEND_KEYSTROKE,
            Capability.OBSERVE_UI, Capability.CLICK_ELEMENT, Capability.KEY_COMBO,
        }
