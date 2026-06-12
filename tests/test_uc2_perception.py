"""UC2 — Screen perception: headless unit tests.

No GUI, Screen Recording grant, or API key required. Covers the observation
fusion, the AX-element text rendering, the permission-check surface, the
no-image / no-client fallbacks, and import-safety. Live focused-window capture is
covered on-device by scripts/ax_smoke.py and the perception smoke.

Run:  ./.venv/bin/python -m pytest tests/test_uc2_perception.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import perception
from action_executor import ActionResult, Capability


class _AXStub:
    """Minimal executor exposing observe_ui (the only method build_observation uses)."""

    def __init__(self, elements, ok=True):
        self._els = elements
        self._ok = ok

    async def observe_ui(self, *, app=None, max_elements=250, task_id=None):
        if not self._ok:
            return ActionResult.failure(Capability.OBSERVE_UI, error="x", message="nope")
        return ActionResult.success(
            Capability.OBSERVE_UI,
            data={"app": app or "frontmost", "elements": self._els})


ELS = [
    {"ref": "e0", "role": "AXWindow", "title": "Untitled", "value": "", "enabled": True, "frame": [0, 0, 800, 600]},
    {"ref": "e1", "role": "AXButton", "title": "Submit", "value": "", "enabled": True, "frame": [10, 20, 80, 24]},
    {"ref": "e2", "role": "AXTextField", "title": "", "value": "hello", "enabled": True, "frame": [10, 60, 200, 24]},
]


def run(c):
    return asyncio.run(c)


def test_import_safe_and_permission_bool():
    assert isinstance(perception.screen_recording_trusted(), bool)


def test_elements_as_text():
    txt = perception.elements_as_text(ELS)
    assert "[e1] AXButton — Submit" in txt
    assert "[e2] AXTextField — hello" in txt


def test_elements_as_text_truncates():
    many = [{"ref": f"e{i}", "role": "AXButton", "title": f"b{i}", "value": ""} for i in range(80)]
    txt = perception.elements_as_text(many, limit=10)
    assert "+70 more" in txt


def test_build_observation_with_image(monkeypatch):
    async def fake_capture(app=None, max_dim=1366):
        return {"b64": "AAAA", "media_type": "image/png", "width": 1366, "height": 900,
                "window_frame": [0, 0, 800, 600]}
    monkeypatch.setattr(perception, "capture_focused_window", fake_capture)
    monkeypatch.setattr(perception, "screen_recording_trusted", lambda: True)
    obs = run(perception.build_observation(_AXStub(ELS), app="TextEdit"))
    assert obs["app"] == "TextEdit"
    assert obs["ax_ok"] is True
    assert len(obs["elements"]) == 3
    assert obs["image"]["width"] == 1366
    # window_frame prefers the captured image's frame
    assert obs["window_frame"] == [0, 0, 800, 600]


def test_build_observation_no_image_falls_back_to_ax_frame(monkeypatch):
    async def no_capture(app=None, max_dim=1366):
        return None
    monkeypatch.setattr(perception, "capture_focused_window", no_capture)
    obs = run(perception.build_observation(_AXStub(ELS)))
    assert obs["image"] is None
    # falls back to the AXWindow element's frame
    assert obs["window_frame"] == [0, 0, 800, 600]
    assert len(obs["elements"]) == 3


def test_describe_observation_ax_only_no_client():
    """No client + no image → deterministic AX-derived sentence (no network)."""
    obs = {"app": "TextEdit", "elements": ELS, "image": None}
    out = run(perception.describe_observation(obs, anthropic_client=None))
    assert "TextEdit" in out and "elements" in out


def test_describe_observation_empty():
    obs = {"app": "frontmost", "elements": [], "image": None}
    out = run(perception.describe_observation(obs, anthropic_client=None))
    assert "couldn't read the screen" in out.lower()


if __name__ == "__main__":
    import traceback
    # pytest's monkeypatch isn't available standalone; run only the no-fixture tests.
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v) and "monkeypatch" not in v.__code__.co_varnames]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed (run via pytest for monkeypatch tests)")
    sys.exit(1 if failed else 0)
