"""UC3 — NL target resolution: headless unit tests.

No GUI/API key. A FakeClient returns canned JSON for the AX-pick and vision-point
model calls (told apart by whether the message carries an image block), so the
resolver's decision logic — AX-first, vision-fallback, ambiguous/miss, and the
pixel→screen coordinate mapping — is fully exercised offline.

Run:  ./.venv/bin/python -m pytest tests/test_uc3_resolver.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import target_resolver as tr


class _Resp:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class FakeClient:
    """Returns ax_json for text-only calls, vision_json when an image is present."""

    def __init__(self, ax_json="{}", vision_json='{"found": false}'):
        self.ax_json = ax_json
        self.vision_json = vision_json
        self.messages = self
        self.calls = []

    async def create(self, **kw):
        content = kw["messages"][0]["content"]
        is_vision = isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "image" for b in content)
        self.calls.append("vision" if is_vision else "ax")
        return _Resp(self.vision_json if is_vision else self.ax_json)


ELS = [
    {"ref": "e0", "role": "AXWindow", "title": "App", "value": "", "enabled": True, "frame": [0, 0, 800, 600]},
    {"ref": "e1", "role": "AXButton", "title": "Submit", "value": "", "enabled": True, "frame": [10, 20, 80, 24]},
    {"ref": "e2", "role": "AXButton", "title": "Cancel", "value": "", "enabled": True, "frame": [110, 20, 80, 24]},
    {"ref": "e3", "role": "AXTextField", "title": "Email", "value": "", "enabled": True, "frame": [10, 60, 200, 24]},
    {"ref": "e4", "role": "AXButton", "title": "Save", "value": "", "enabled": True, "frame": [10, 100, 80, 24]},
]
IMG = {"b64": "AAAA", "media_type": "image/png", "width": 400, "height": 300}


def obs(elements=ELS, image=None, frame=None):
    return {"app": "App", "elements": elements, "image": image,
            "window_frame": frame or [0, 0, 800, 600]}


def run(c):
    return asyncio.run(c)


def test_ax_single_match():
    c = FakeClient(ax_json='{"ref": "e1"}')
    r = run(tr.resolve(obs(), "Submit", c))
    assert r.status == "ref" and r.ref == "e1" and r.via == "ax" and r.label == "Submit"


def test_ax_ambiguous_asks():
    c = FakeClient(ax_json='{"ambiguous": ["e1", "e4"]}')
    r = run(tr.resolve(obs(), "the button", c))
    assert r.status == "ambiguous"
    assert {a["ref"] for a in r.alternatives} == {"e1", "e4"}
    assert "which one" in r.message.lower()


def test_ax_miss_no_image_is_honest():
    c = FakeClient(ax_json='{"found": false}')
    r = run(tr.resolve(obs(), "Frobnicate", c))
    assert r.status == "miss" and "Frobnicate" in r.message


def test_ax_invalid_ref_is_miss():
    c = FakeClient(ax_json='{"ref": "e99"}')  # model hallucinated a ref
    r = run(tr.resolve(obs(), "Submit", c))
    assert r.status == "miss"


def test_ax_miss_falls_back_to_vision():
    c = FakeClient(ax_json='{"found": false}', vision_json='{"found": true, "x": 200, "y": 150}')
    r = run(tr.resolve(obs(image=IMG, frame=[100, 200, 800, 600]), "Submit", c))
    assert r.status == "point" and r.via == "vision"
    # pixel (200,150) in a 400x300 image over window [100,200,800,600]:
    #   sx = 100 + 200*(800/400)=500 ; sy = 200 + 150*(600/300)=500
    assert r.point == (500.0, 500.0)
    assert "ax" in c.calls and "vision" in c.calls  # tried AX first


def test_thin_ax_goes_straight_to_vision():
    thin = [{"ref": "e0", "role": "AXGroup", "title": "", "value": "", "enabled": True, "frame": [0, 0, 400, 300]}]
    c = FakeClient(vision_json='{"found": true, "x": 100, "y": 60}')
    r = run(tr.resolve(obs(elements=thin, image=IMG, frame=[0, 0, 400, 300]), "Login", c))
    assert r.status == "point" and r.via == "vision"
    assert c.calls == ["vision"]  # skipped AX entirely (thin tree)


def test_no_match_anywhere_is_miss():
    c = FakeClient(ax_json='{"found": false}', vision_json='{"found": false}')
    r = run(tr.resolve(obs(image=IMG), "Unicorn", c))
    assert r.status == "miss"


def test_parse_json_tolerates_fences():
    assert tr._parse_json('```json\n{"ref": "e1"}\n```') == {"ref": "e1"}
    assert tr._parse_json('here: {"found": false} ok') == {"found": False}
    assert tr._parse_json("not json") is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
