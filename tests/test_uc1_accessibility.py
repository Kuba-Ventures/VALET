"""UC1 — Accessibility executor: headless unit tests.

These run WITHOUT a GUI session, Accessibility grant, or API key. They verify the
seam, the safety tiering, the routing, and the import-safety — everything that
doesn't require synthesizing real input. The live click/type/observe behaviour is
covered by scripts/ax_smoke.py on-device.

Run:  ./.venv/bin/python -m pytest tests/test_uc1_accessibility.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import safety
from action_executor import ActionExecutor, ActionResult, Capability, UIElement
from composite_executor import CompositeExecutor
from safe_executor import SafeExecutor


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #
class _Stub(ActionExecutor):
    """Concrete ActionExecutor whose every abstract method is not_supported.
    The three UC1 methods inherit the base defaults (also not_supported)."""

    name = "stub"

    async def open_app(self, app, *, task_id=None):
        return ActionResult.not_supported(Capability.OPEN_APP, reason="stub")
    async def open_path(self, path, *, task_id=None):
        return ActionResult.not_supported(Capability.OPEN_PATH, reason="stub")
    async def run_app_command(self, app, command, *, task_id=None):
        return ActionResult.not_supported(Capability.RUN_APP_COMMAND, reason="stub")
    async def send_keystroke(self, app, text, *, press_enter=False, task_id=None):
        return ActionResult.not_supported(Capability.SEND_KEYSTROKE, reason="stub")
    async def read_file(self, path):
        return ActionResult.not_supported(Capability.READ_FILE, reason="stub")
    async def write_file(self, path, content):
        return ActionResult.not_supported(Capability.WRITE_FILE, reason="stub")
    async def move_file(self, src, dst):
        return ActionResult.not_supported(Capability.MOVE_FILE, reason="stub")
    async def delete_file(self, path):
        return ActionResult.not_supported(Capability.DELETE_FILE, reason="stub")
    async def list_folder(self, path):
        return ActionResult.not_supported(Capability.LIST_FOLDER, reason="stub")
    async def navigate(self, url, *, browser="chrome"):
        return ActionResult.not_supported(Capability.NAVIGATE, reason="stub")
    async def run_script(self, script):
        return ActionResult.not_supported(Capability.RUN_SCRIPT, reason="stub")
    async def is_app_scriptable(self, app):
        return False


class _AXStub(_Stub):
    """Fallback backend that implements the UC1 primitives."""

    name = "axstub"

    def __init__(self):
        self.clicks = []

    async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
        els = [UIElement(ref="e0", role="AXButton", title="Submit", enabled=True,
                         frame=[10, 20, 80, 24])]
        return ActionResult.success(Capability.OBSERVE_UI,
                                    data={"app": app or "frontmost",
                                          "elements": [e.to_dict() for e in els]})

    async def click_element(self, *, ref=None, point=None, app=None, task_id=None):
        self.clicks.append((ref, point))
        return ActionResult.success(Capability.CLICK_ELEMENT, message="Clicked, sir.")

    async def key_combo(self, combo, *, app=None, task_id=None):
        return ActionResult.success(Capability.KEY_COMBO, message="Done, sir.")


class _Confirm:
    """Stand-in for safety.ConfirmationManager: records calls, returns `allow`."""

    def __init__(self, allow):
        self.allow = allow
        self.calls = []

    async def request(self, **kw):
        self.calls.append(kw)
        return self.allow


class _Kill:
    def is_engaged(self):
        return False


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_new_capabilities_exist():
    assert Capability.OBSERVE_UI.value == "observe_ui"
    assert Capability.CLICK_ELEMENT.value == "click_element"
    assert Capability.KEY_COMBO.value == "key_combo"


def test_safety_tiers():
    assert safety.classify(Capability.OBSERVE_UI).tier == safety.RiskTier.AUTO
    assert safety.classify(Capability.CLICK_ELEMENT).tier == safety.RiskTier.CONFIRM
    assert safety.classify(Capability.KEY_COMBO).tier == safety.RiskTier.CONFIRM


def test_base_defaults_not_supported():
    """A backend that doesn't override the UC1 methods (e.g. AppleScript) reports
    not_supported — never raises — so the composite can fall through."""
    s = _Stub()
    for res in (run(s.observe_ui()), run(s.click_element(ref="e0")), run(s.key_combo("cmd+s"))):
        assert res.supported is False
        assert res.ok is False


def test_composite_routes_to_fallback():
    comp = CompositeExecutor(_Stub(), _AXStub())
    obs = run(comp.observe_ui())
    assert obs.ok and obs.data["elements"][0]["title"] == "Submit"
    clk = run(comp.click_element(ref="e0"))
    assert clk.ok
    kc = run(comp.key_combo("cmd+s"))
    assert kc.ok


def test_safeexecutor_observe_is_tier0():
    """Observation runs without a confirmation prompt."""
    conf = _Confirm(allow=False)  # would deny IF asked
    se = SafeExecutor(_AXStub(), confirmations=conf, kill_switch=_Kill())
    res = run(se.observe_ui())
    assert res.ok
    assert conf.calls == []  # never prompted


def test_safeexecutor_click_requires_confirmation():
    ax = _AXStub()
    denied = SafeExecutor(ax, confirmations=_Confirm(allow=False), kill_switch=_Kill())
    res = run(denied.click_element(ref="e0"))
    assert res.ok is False and res.error == "denied"
    assert ax.clicks == []  # delegate never reached

    allowed = SafeExecutor(ax, confirmations=_Confirm(allow=True), kill_switch=_Kill())
    res2 = run(allowed.click_element(ref="e0"))
    assert res2.ok and ax.clicks == [("e0", None)]


def test_safeexecutor_keycombo_requires_confirmation():
    conf = _Confirm(allow=True)
    se = SafeExecutor(_AXStub(), confirmations=conf, kill_switch=_Kill())
    res = run(se.key_combo("cmd+s", app="TextEdit"))
    assert res.ok
    assert len(conf.calls) == 1 and "cmd+s" in conf.calls[0]["summary"]


def test_accessibility_executor_import_safe():
    """Importing the backend never raises, and is_trusted() returns a bool."""
    import accessibility_executor as ax
    assert isinstance(ax.is_trusted(), bool)
    inst = ax.AccessibilityExecutor()
    # With no grant / no pyobjc this is a clean failure or not_supported, not a raise.
    res = run(inst.observe_ui())
    assert isinstance(res, ActionResult)


def test_key_combo_parsing():
    import accessibility_executor as ax
    if not ax._PYOBJC:
        return  # parsing needs Quartz flag constants; skip on a non-pyobjc host
    flags, keycode, literal = ax._parse_combo("cmd+s")
    assert flags & ax.Quartz.kCGEventFlagMaskCommand
    assert keycode == ax._KEYCODES["s"]
    flags2, kc2, lit2 = ax._parse_combo("cmd+shift+4")
    assert flags2 & ax.Quartz.kCGEventFlagMaskShift
    assert kc2 == ax._KEYCODES["4"]


if __name__ == "__main__":
    # Allow plain `python tests/test_uc1_accessibility.py`
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
