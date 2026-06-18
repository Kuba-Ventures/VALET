"""Natural-language target resolution (Phase K — UC3).

"Click on Submit" / "type my email in the address field" → a concrete action.

Resolution order (AX-first, vision-fallback — the track's standing rule):
  1. **AX pick (primary):** the model chooses the single best element `ref` from
     the UC2 observation's accessibility snapshot (real `ref`s, real frames).
  2. **Vision point (fallback):** when the AX tree is empty/thin or the AX pick
     misses, the model returns pixel coordinates from the focused-window
     screenshot, mapped back to absolute screen coordinates for a point-click.
  3. **Honest outcomes:** ambiguous match → ask; nothing found → fail honestly
     ("I don't see a 'Submit' control, sir"). Never a wild click.

This module only DECIDES (pure, model-driven, unit-testable). Execution stays in
the caller, which runs the resolved action through the safety-gated executor
(confirm card + kill switch) — a resolved target is never auto-clicked here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("valet.resolver")

# Below this many interactive elements the AX tree is "thin" (Electron/canvas/
# custom-drawn UIs) and vision is tried first/also.
_THIN_AX = 4


@dataclass
class Resolution:
    """Outcome of resolving a description against an observation."""

    status: str                       # "ref" | "point" | "ambiguous" | "miss"
    ref: Optional[str] = None
    point: Optional[tuple] = None      # absolute screen (x, y)
    frame: Optional[list] = None       # [x, y, w, h] global screen rect (ref path only)
    label: str = ""
    alternatives: list = field(default_factory=list)  # [{ref,label}] when ambiguous
    via: str = ""                      # "ax" | "vision"
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status, "ref": self.ref, "point": self.point,
            "frame": self.frame, "label": self.label,
            "alternatives": self.alternatives, "via": self.via,
            "message": self.message,
        }


def _parse_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a model reply (tolerates code fences)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if 0 <= a < b:
            try:
                return json.loads(t[a:b + 1])
            except Exception:
                return None
    return None


def _elements_block(elements: list, limit: int = 80) -> str:
    lines = []
    for e in elements[:limit]:
        label = (e.get("title") or e.get("value") or "").strip()
        en = "" if e.get("enabled", True) else " (disabled)"
        lines.append(f'{e["ref"]}: {e.get("role","")} "{label[:60]}"{en}')
    return "\n".join(lines)


async def _ax_pick(elements: list, description: str, client, intent: str) -> Resolution:
    """Ask the model to choose the best element ref for `description`."""
    sys = (
        "You map a user's natural-language UI target to ONE accessibility element. "
        "You are given a numbered list of elements (ref: role \"label\"). Return STRICT "
        "JSON only: {\"ref\": \"<ref>\"} for a confident single match; "
        "{\"ambiguous\": [\"<ref>\", ...]} when two or more elements match about "
        "equally; {\"found\": false} when nothing matches. Prefer interactive controls "
        "(buttons, fields, links, menu items). Never invent a ref that isn't listed."
    )
    user = (f'User wants to {intent}: "{description}".\n\nElements:\n'
            f'{_elements_block(elements)}\n\nReturn JSON.')
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=200,
            system=sys, messages=[{"role": "user", "content": user}],
        )
        data = _parse_json(resp.content[0].text) or {}
    except Exception as e:
        log.warning("ax pick failed: %s", e)
        return Resolution(status="miss", via="ax")

    valid = {e["ref"]: e for e in elements}
    if data.get("found") is False:
        return Resolution(status="miss", via="ax")
    amb = [r for r in (data.get("ambiguous") or []) if r in valid]
    if len(amb) >= 2:
        alts = [{"ref": r, "label": (valid[r].get("title") or valid[r].get("value") or valid[r].get("role"))} for r in amb]
        return Resolution(status="ambiguous", alternatives=alts, via="ax",
                          message=f"I see a few things matching '{description}', sir — which one?")
    ref = data.get("ref")
    if ref in valid:
        e = valid[ref]
        return Resolution(status="ref", ref=ref, via="ax", frame=e.get("frame"),
                          label=(e.get("title") or e.get("value") or e.get("role") or ref))
    return Resolution(status="miss", via="ax")


async def _vision_point(observation: dict, description: str, client) -> Optional[Resolution]:
    """Ask the model for the on-screen pixel of `description`; map to screen coords."""
    img = observation.get("image")
    wf = observation.get("window_frame")
    if not img or not wf:
        return None
    sys = (
        "You locate a UI target in a screenshot. Return STRICT JSON only: "
        "{\"found\": true, \"x\": <int>, \"y\": <int>} with x,y the PIXEL coordinates "
        "of the CENTER of the target in THIS image, or {\"found\": false}."
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=120, system=sys,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": img["media_type"], "data": img["b64"]}},
                {"type": "text", "text": f'Where is "{description}"? Return JSON.'},
            ]}],
        )
        data = _parse_json(resp.content[0].text) or {}
    except Exception as e:
        log.warning("vision point failed: %s", e)
        return None
    if not data.get("found") or "x" not in data or "y" not in data:
        return None
    iw, ih = img.get("width") or 1, img.get("height") or 1
    sx = wf[0] + float(data["x"]) * (wf[2] / iw)
    sy = wf[1] + float(data["y"]) * (wf[3] / ih)
    return Resolution(status="point", point=(sx, sy), via="vision", label=description)


async def resolve(observation: dict, description: str, client, *, intent: str = "click") -> Resolution:
    """Resolve `description` to a concrete target against `observation`.

    AX pick first; vision point as a fallback when AX is thin or misses; an
    honest miss when neither finds it."""
    elements = observation.get("elements") or []
    has_image = bool(observation.get("image"))

    # Thin AX tree (Electron/canvas): go straight to vision when we have an image.
    if has_image and len(elements) < _THIN_AX and client:
        v = await _vision_point(observation, description, client)
        if v:
            return v

    if elements and client:
        pick = await _ax_pick(elements, description, client, intent)
        if pick.status in ("ref", "ambiguous"):
            return pick
        # AX missed — fall back to vision before giving up.
        if has_image:
            v = await _vision_point(observation, description, client)
            if v:
                return v
        return Resolution(status="miss", via="ax",
                          message=f"I don't see a '{description}' to {intent}, sir.")

    # No AX list — vision only.
    if has_image and client:
        v = await _vision_point(observation, description, client)
        if v:
            return v
    return Resolution(status="miss",
                      message=f"I don't see a '{description}' to {intent}, sir.")
