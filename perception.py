"""Screen perception (Phase K — UC2).

Turns "what's on screen" into a single structured **observation**:

    observation = focused-window screenshot  +  the UC1 AX element snapshot

The screenshot is captured for the FOCUSED WINDOW only (not the whole display),
downscaled to a token-sane size, and paired with the numbered, ref-addressable
element list `observe_ui` produces — so the model both *sees* the window and has
the real, clickable elements (the foundation UC3's "click on Submit" stands on).

Screen capture needs the macOS **Screen Recording** permission; this module owns
the real `CGPreflightScreenCaptureAccess` check and the prompt. Import-safe: with
pyobjc absent every entry point degrades cleanly (no image, never raises).

Privacy: the screenshot bytes are an in-memory observation sent to the model via
the licensed proxy. They are never written to disk here and never logged as an
analytics payload (callers must keep `capture_input=False` on the vision call).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("valet.perception")

try:
    import Quartz  # type: ignore
    from AppKit import NSWorkspace  # type: ignore

    _PYOBJC = True
except Exception:
    _PYOBJC = False

# Token-sane longest-edge for the window screenshot. Anthropic vision tiles large
# images; ~1366px keeps the focused window legible without burning tokens.
_DEFAULT_MAX_DIM = 1366


# --------------------------------------------------------------------------- #
# Screen Recording permission (real TCC checks)
# --------------------------------------------------------------------------- #
def screen_recording_trusted() -> bool:
    """True if this process may capture the screen. No prompt (preflight)."""
    if not _PYOBJC:
        return False
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return False


def request_screen_recording() -> bool:
    """Fire the native Screen Recording prompt if not yet granted. Returns the
    current state (the grant lands in System Settings and needs an app relaunch
    to take effect, so this typically still reads False right after)."""
    if not _PYOBJC:
        return False
    try:
        Quartz.CGRequestScreenCaptureAccess()
    except Exception:
        pass
    return screen_recording_trusted()


# --------------------------------------------------------------------------- #
# Focused-window capture
# --------------------------------------------------------------------------- #
def _is_valet(name: Optional[str]) -> bool:
    """Our own app/windows — never the target of an observation (Phase 2)."""
    return "valet" in (name or "").lower()


def _valet_pids(ws) -> set:
    pids = set()
    try:
        for r in ws.runningApplications():
            if _is_valet(r.localizedName()):
                pids.add(int(r.processIdentifier()))
    except Exception:
        pass
    return pids


def _topmost_non_valet_pid(ws) -> Optional[int]:
    """The owner pid of the topmost on-screen window that isn't VALET's, using the
    window stacking order — i.e. the app the user was looking at *behind* Vee."""
    try:
        opts = (Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements)
        info = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
    except Exception:
        return None
    skip = _valet_pids(ws)
    for w in info or []:
        if int(w.get(Quartz.kCGWindowLayer, 0)) != 0:
            continue
        pid = w.get(Quartz.kCGWindowOwnerPID)
        if pid is None or int(pid) in skip or _is_valet(w.get(Quartz.kCGWindowOwnerName, "")):
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        if b.get("Width", 0) < 200 or b.get("Height", 0) < 150:
            continue  # skip slivers / menubar items
        return int(pid)
    return None


def _pid_for_app(app: Optional[str]) -> Optional[int]:
    """App name → running pid. None → the FRONTMOST NON-VALET app (Phase 2): when
    the user talks to Vee, VALET is frontmost, but they mean the app they were
    just using — so skip our own windows and pick the app stacked just behind us."""
    if not _PYOBJC:
        return None
    try:
        ws = NSWorkspace.sharedWorkspace()
        if app:
            for r in ws.runningApplications():
                if (r.localizedName() or "").lower() == app.lower():
                    return int(r.processIdentifier())
            return None
        front = ws.frontmostApplication()
        if front and not _is_valet(front.localizedName()):
            return int(front.processIdentifier())
        return _topmost_non_valet_pid(ws) or (int(front.processIdentifier()) if front else None)
    except Exception as e:
        log.warning("pid lookup for %r failed: %s", app, e)
    return None


def _frontmost_window_id(pid: int):
    """The focused window's CGWindowID for `pid` (+ its bounds), or None.

    CGWindowListCopyWindowInfo returns windows front-to-back, so the first
    normal-layer window owned by the pid is the focused one."""
    try:
        opts = (Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements)
        info = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
    except Exception as e:
        log.warning("window list failed: %s", e)
        return None
    for w in info or []:
        if w.get(Quartz.kCGWindowOwnerPID) != pid:
            continue
        if int(w.get(Quartz.kCGWindowLayer, 0)) != 0:  # skip menubar/panels/etc.
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        if (b.get("Width", 0) < 40) or (b.get("Height", 0) < 40):
            continue  # skip slivers
        return int(w.get(Quartz.kCGWindowNumber)), {
            "x": float(b.get("X", 0)), "y": float(b.get("Y", 0)),
            "w": float(b.get("Width", 0)), "h": float(b.get("Height", 0)),
        }
    return None


async def _run(*cmd: str, timeout: float = 10.0) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1
    return proc.returncode or 0


async def capture_focused_window(app: Optional[str] = None,
                                 max_dim: int = _DEFAULT_MAX_DIM) -> Optional[dict]:
    """Capture the focused window of `app` (or the frontmost app), downscale to
    `max_dim` longest edge, return {b64, media_type, width, height, window_frame}
    — or None if pyobjc/permission/window are unavailable.

    No prompt is fired here: the caller checks `screen_recording_trusted()` and
    surfaces the permission ask in the UI."""
    if not _PYOBJC:
        return None
    if not screen_recording_trusted():
        return None
    pid = _pid_for_app(app)
    if pid is None:
        return None
    found = await asyncio.to_thread(_frontmost_window_id, pid)
    if not found:
        return None
    win_id, frame = found

    tmp = Path(tempfile.mkdtemp(prefix="valet_obs_")) / "win.png"
    # -l <id>: just that window;  -o: no shadow;  -x: silent.
    rc = await _run("screencapture", "-x", "-o", "-l", str(win_id), str(tmp))
    if rc != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        log.warning("screencapture -l %s failed (rc=%s)", win_id, rc)
        _cleanup(tmp)
        return None
    # Downscale (sips resamples in place to longest-edge = max_dim).
    await _run("sips", "-Z", str(max_dim), str(tmp))
    try:
        data = tmp.read_bytes()
        w, h = await _dimensions(tmp)
        return {
            "b64": base64.b64encode(data).decode(),
            "media_type": "image/png",
            "width": w, "height": h,
            "window_frame": [frame["x"], frame["y"], frame["w"], frame["h"]],
        }
    finally:
        _cleanup(tmp)


async def _dimensions(path: Path) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        "sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    w = h = 0
    for line in out.decode(errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("pixelWidth:"):
            w = int(s.split(":")[1])
        elif s.startswith("pixelHeight:"):
            h = int(s.split(":")[1])
    return w, h


def _cleanup(tmp: Path) -> None:
    try:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Observation = screenshot + AX snapshot
# --------------------------------------------------------------------------- #
async def build_observation(executor, app: Optional[str] = None,
                            max_dim: int = _DEFAULT_MAX_DIM) -> dict:
    """One observation: the focused-window screenshot + the UC1 AX element list.

    `executor` is the safety-gated control executor (observe_ui is Tier 0, so it
    runs straight through). The image may be None when Screen Recording isn't
    granted — the AX snapshot still stands on its own."""
    ax = await executor.observe_ui(app=app)
    elements = ax.data.get("elements", []) if ax.ok else []
    app_name = ax.data.get("app", app or "frontmost") if ax.ok else (app or "frontmost")
    image = await capture_focused_window(app=app, max_dim=max_dim)
    win_frame = image["window_frame"] if image else next(
        (e["frame"] for e in elements if e["role"] == "AXWindow"), None)
    return {
        "app": app_name,
        "elements": elements,
        "window_frame": win_frame,
        "image": image,                      # {b64, media_type, width, height} | None
        "screen_recording": screen_recording_trusted(),
        "ax_ok": ax.ok,
    }


def elements_as_text(elements: list, limit: int = 60) -> str:
    """Compact numbered list of interactive elements for the model prompt."""
    lines = []
    for e in elements[:limit]:
        label = (e.get("title") or e.get("value") or "").strip()
        lines.append(f"[{e['ref']}] {e.get('role','')}" + (f" — {label[:60]}" if label else ""))
    if len(elements) > limit:
        lines.append(f"… (+{len(elements) - limit} more)")
    return "\n".join(lines)


async def describe_observation(observation: dict, anthropic_client) -> str:
    """Send the observation (window image + AX element list) to the model via the
    proxy and return a short spoken description. Falls back to an AX/title-only
    description when there's no image or no client."""
    elements = observation.get("elements", [])
    app = observation.get("app", "the focused app")
    ax_text = elements_as_text(elements)

    if observation.get("image") and anthropic_client:
        img = observation["image"]
        try:
            resp = await anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                system=(
                    "You are VALET looking at the user's FOCUSED window. You are given a "
                    "screenshot and a list of its accessibility elements. Reply in ONE short, "
                    "natural sentence for the voice — name the app and what the user's working "
                    "on, plus the single most notable detail. No markdown, no lists; spoken "
                    "aloud, so keep it tight (e.g. \"You're in Affinity Publisher on a "
                    "toddler-toothbrush survey one-pager, sir\")."
                ),
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": img["media_type"], "data": img["b64"]}},
                    {"type": "text", "text":
                        f"Focused app: {app}.\nInteractive elements:\n{ax_text}\n\n"
                        "What's on my screen right now?"},
                ]}],
            )
            return resp.content[0].text
        except Exception as e:
            log.warning("vision observation failed, falling back to AX: %s", e)

    # No image (permission off) or no client — describe from the AX snapshot alone.
    if elements:
        if anthropic_client:
            try:
                resp = await anthropic_client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                    system=("You are VALET. From the focused app and its accessibility "
                            "elements, say what the user appears to be doing in 1-2 sentences. "
                            "Natural voice, no markdown."),
                    messages=[{"role": "user", "content":
                               f"Focused app: {app}.\nElements:\n{ax_text}"}],
                )
                return resp.content[0].text
            except Exception:
                pass
        top = next((e for e in elements if e["role"] == "AXWindow"), None)
        title = (top.get("title") if top else "") or app
        return f"You're in {app} — {title}, sir. I count {len(elements)} elements on screen."
    return ("I couldn't read the screen, sir. Screen Recording or Accessibility "
            "permission may be needed.")


async def summarize_observation(observation: dict, anthropic_client) -> str:
    """Read the FOCUSED content (window screenshot + AX text) and return a tight,
    spoken summary that leads with the gist and calls out what the user needs to
    do. App-agnostic: an email, a doc, a dashboard, an article — whatever's in
    front. Falls back to the AX text alone when there's no image or no client."""
    elements = observation.get("elements", [])
    app = observation.get("app", "the focused app")
    ax_text = elements_as_text(elements)

    system = (
        "You are VALET reading the user's FOCUSED window aloud. Summarize what's in "
        "front of them and, crucially, what they need to DO about it. Lead with one "
        "plain-sentence gist, then the action items as a short spoken run ('Two "
        "things, sir: first … then …') — at most three, only the ones that are "
        "actually there. If there's nothing to act on, say so and give the gist "
        "only. British butler tone, dry and economical. No markdown, no bullet "
        "characters, no headings — this is spoken. Keep it under about 60 words."
    )
    user_text = (f"Focused app: {app}.\nVisible text / elements:\n{ax_text}\n\n"
                 "Summarize this and tell me what I need to do.")

    if observation.get("image") and anthropic_client:
        img = observation["image"]
        try:
            resp = await anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                system=system,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": img["media_type"], "data": img["b64"]}},
                    {"type": "text", "text": user_text},
                ]}],
            )
            return resp.content[0].text
        except Exception as e:
            log.warning("vision summary failed, falling back to AX: %s", e)

    # No image (permission off) or no client — summarize from the AX snapshot alone.
    if elements and anthropic_client:
        try:
            resp = await anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": user_text}],
            )
            return resp.content[0].text
        except Exception:
            pass
    return ("I couldn't read the screen well enough to summarize, sir. Screen "
            "Recording or Accessibility permission may be needed.")


async def build_fix_brief(observation: dict, anthropic_client) -> dict:
    """Turn the FOCUSED content (an error report, an issue email, a failing
    dashboard…) into a concrete engineering task for Claude Code.

    Returns {"title": str, "prompt": str}. title is "NO_TASK" when the screen
    doesn't describe a fixable software issue. App-agnostic; uses the screenshot
    + AX text. Never invents specifics that aren't on screen."""
    elements = observation.get("elements", [])
    app = observation.get("app", "the focused app")
    ax_text = elements_as_text(elements)
    empty = {"title": "NO_TASK", "prompt": ""}
    if not anthropic_client:
        return empty

    system = (
        "You are VALET, turning what's on the user's screen into a precise task for "
        "Claude Code (a coding agent that will edit a repo). Read the screenshot and "
        "the accessibility text. Produce:\n"
        "- title: one imperative line, <=10 words (e.g. 'Fix Stripe webhook 404s in "
        "sandbox').\n"
        "- prompt: a concrete, self-contained instruction telling Claude Code what to "
        "investigate and fix, quoting the SPECIFICS visible on screen (exact error "
        "text, endpoints, IDs, status codes, feature/file names). Do not invent "
        "anything that isn't shown; don't guess repo paths.\n"
        "If the screen doesn't describe a fixable software issue, set title to "
        "'NO_TASK'. Reply as STRICT JSON only: {\"title\":\"...\",\"prompt\":\"...\"}."
    )
    user_text = (f"Focused app: {app}.\nVisible text / elements:\n{ax_text}\n\n"
                 "Turn this into a fix task for Claude Code.")
    content = [{"type": "text", "text": user_text}]
    if observation.get("image"):
        img = observation["image"]
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": img["media_type"], "data": img["b64"]}})
    try:
        resp = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=600, system=system,
            messages=[{"role": "user", "content": content}])
        raw = (resp.content[0].text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{"):] if "{" in raw else raw
        a, b = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[a:b + 1]) if 0 <= a < b else {}
        title = (data.get("title") or "NO_TASK").strip()
        prompt = (data.get("prompt") or "").strip()
        if not title or not prompt:
            return empty
        return {"title": title, "prompt": prompt}
    except Exception as e:
        log.warning("build_fix_brief failed: %s", e)
        return empty
