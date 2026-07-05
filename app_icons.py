"""Extract macOS app icons for the account dashboard's "Top apps" panel.

Given an app display name (the same privacy-safe label used in the usage
breakdown), resolve its `.app` bundle via LaunchServices, render its icon to a
small PNG, and base64-encode it. Results are memoised in-process so we only pay
the AppKit round-trip once per app per run.

Everything here is best-effort and macOS-only: any failure (app not found,
AppKit unavailable, off-platform) returns ``None`` and the dashboard falls back
to a letter badge. No user content is involved — an app's icon is identical for
everyone who has that app installed, so it is safe to sync and share.
"""

from __future__ import annotations

import base64
import logging
import sys

log = logging.getLogger("valet.app_icons")

# Rendered icon edge, in points. 64px keeps the base64 payload a few KB while
# staying crisp for the ~20px dashboard slot on a retina display.
_ICON_PX = 64
# Hard ceiling so a pathological icon can never bloat the sync payload. A 64px
# PNG is normally 4–10 KB (≈5–14 KB base64); anything larger is dropped.
_MAX_B64_LEN = 40_000

# app label -> base64 PNG (or None if we've already tried and failed).
_cache: dict[str, str | None] = {}


def _render_icon_b64(app_name: str) -> str | None:
    """Resolve ``app_name`` to its bundle and return a base64 64px PNG, or None.

    Draws the LaunchServices icon into a fixed-size offscreen bitmap so the
    output is always ~64px regardless of the source `.icns` resolution — that
    both normalises the look and bounds the payload size.
    """
    try:
        from AppKit import (
            NSWorkspace,
            NSBitmapImageRep,
            NSGraphicsContext,
            NSBitmapImageFileTypePNG,
            NSCompositingOperationSourceOver,
            NSDeviceRGBColorSpace,
        )
        from Foundation import NSMakeRect, NSZeroRect
    except Exception:  # AppKit unavailable (off-platform / headless bundle)
        return None

    try:
        ws = NSWorkspace.sharedWorkspace()
        path = ws.fullPathForApplication_(app_name)
        if not path:
            return None
        icon = ws.iconForFile_(path)
        if icon is None:
            return None

        # Draw into an explicit _ICON_PX-square bitmap. Going through a bitmap
        # rep (rather than NSImage.lockFocus) pins the output to a true 64px —
        # lockFocus would honour the screen's 2× backing scale and double both
        # the pixels and the payload size.
        rep = (
            NSBitmapImageRep.alloc()
            .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None,
                _ICON_PX,
                _ICON_PX,
                8,
                4,
                True,
                False,
                NSDeviceRGBColorSpace,
                0,
                0,
            )
        )
        if rep is None:
            return None

        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        if ctx is None:
            return None
        NSGraphicsContext.saveGraphicsState()
        try:
            NSGraphicsContext.setCurrentContext_(ctx)
            icon.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(0, 0, _ICON_PX, _ICON_PX),
                NSZeroRect,
                NSCompositingOperationSourceOver,
                1.0,
            )
        finally:
            NSGraphicsContext.restoreGraphicsState()

        png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        if png is None:
            return None

        b64 = base64.b64encode(bytes(png)).decode("ascii")
        if not b64 or len(b64) > _MAX_B64_LEN:
            return None
        return b64
    except Exception as e:  # never let icon capture break a sync
        log.debug("icon capture failed for %r: %s", app_name, e)
        return None


def get_app_icon_b64(app_name: str) -> str | None:
    """Base64 PNG for an app's icon, memoised. None off macOS or on any failure."""
    name = (app_name or "").strip()
    if not name or sys.platform != "darwin":
        return None
    if name in _cache:
        return _cache[name]
    result = _render_icon_b64(name)
    _cache[name] = result
    return result
