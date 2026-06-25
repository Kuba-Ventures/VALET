"""
VALET Action Executor — AppleScript-based system actions.

Execute actions IMMEDIATELY, before generating any LLM response.
Each function returns {"success": bool, "confirmation": str}.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from process_events import (
    emit_app_launch,
    emit_error,
    emit_project_event,
    emit_step,
    emit_text_write,
)

log = logging.getLogger("valet.actions")

DESKTOP_PATH = Path.home() / "Desktop"

_CONFIG_PATH = Path(__file__).parent / "config" / "design_partner.json"
_cached_config: dict | None = None


def _design_partner_config() -> dict:
    """Lazy-load config/design_partner.json. Returns {} on any read/parse error."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config
    try:
        if _CONFIG_PATH.exists():
            _cached_config = json.loads(_CONFIG_PATH.read_text())
        else:
            _cached_config = {}
    except Exception as e:
        log.warning(f"design_partner.json unreadable: {e}")
        _cached_config = {}
    return _cached_config


_CLAUDE_MD_TEMPLATE = """# {name}

## Client
<placeholder>

## Value prop
<placeholder>

## Current MVP scope
<placeholder>

## Recent meeting notes
<!-- TODO: VALET will populate from memory layer in a future phase -->
"""


# .vscode/tasks.json scaffold — auto-runs `claude` in a dedicated terminal
# panel inside Cursor on workspace open. First open per workspace prompts the
# user with a Workspace Trust "Allow Automated Tasks" dialog; subsequent opens
# run silently. Keys verified against the VS Code tasks.json 2.0.0 schema:
#   version, tasks[].label, .type, .command, .presentation (reveal/panel/focus),
#   .runOptions.runOn = "folderOpen".
_TASKS_JSON = """{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Start Claude",
      "type": "shell",
      "command": "claude",
      "presentation": {
        "reveal": "always",
        "panel": "dedicated",
        "focus": false
      },
      "runOptions": {
        "runOn": "folderOpen"
      }
    }
  ]
}
"""


def _ensure_cursor_task(project_path: Path, task_id: str | None = None) -> bool:
    """No-clobber scaffold of .vscode/tasks.json that auto-runs claude on open.

    Returns True if the file was newly written, False if it already existed
    (no-clobber — caller has likely customized it). Same pattern as the
    CLAUDE.md scaffolding rule.
    """
    vscode_dir = project_path / ".vscode"
    tasks_path = vscode_dir / "tasks.json"
    if tasks_path.exists():
        return False
    try:
        vscode_dir.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text(_TASKS_JSON)
        log.info(f"Scaffolded {tasks_path}")
        return True
    except Exception as e:
        log.warning(f"Failed to scaffold {tasks_path}: {e}")
        return False


async def _load_warm_context(path: Path) -> None:
    """Background task: load project_context for `path`. Best-effort, never raises."""
    try:
        import project_context
        await project_context.load(path)
    except Exception as e:
        log.warning(f"project_context.load({path}) failed: {e}")


def _project_roots() -> list[Path]:
    """Resolve configured project_roots paths. Defaults to ~/Code + ~/projects.

    Read fresh from config each call (cheap dict get) so config edits take
    effect without a restart. Skips entries that don't expand to real dirs.
    """
    cfg = _design_partner_config()
    raw = cfg.get("project_roots") or ["~/Code", "~/projects"]
    return [Path(r).expanduser() for r in raw]


def _fuzzy_suggestions(name: str, limit: int = 3) -> list[dict]:
    """Top-N likely projects for a name that didn't resolve cleanly.

    Token-overlap scoring against `list_projects()`. Returns empty list when
    no tokens overlap — the caller should then prompt the user to register
    by path rather than offering wrong matches.
    """
    from memory import _normalize_project_key
    key = _normalize_project_key(name)
    if not key:
        return []
    tokens = set(t for t in key.split("-") if t)
    scored: list[tuple[int, dict]] = []
    for proj in list_projects():
        pkey = _normalize_project_key(proj["name"])
        ptokens = set(t for t in pkey.split("-") if t)
        overlap = len(tokens & ptokens)
        if overlap > 0:
            scored.append((overlap, proj))
    scored.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
    return [proj for _, proj in scored[:limit]]


async def _mark_terminal_as_valet(revert_after: float = 5.0):
    """Temporarily set the front Terminal window to Ocean theme, then revert.

    Shows the user VALET is active in that terminal. Reverts after revert_after seconds.
    """
    # Save the current profile, switch to Ocean, then revert
    script_save = (
        'tell application "Terminal"\n'
        '    return name of current settings of front window\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_save,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        original_profile = stdout.decode().strip()

        # Switch to Ocean
        script_set = (
            'tell application "Terminal"\n'
            '    set current settings of front window to settings set "Ocean"\n'
            'end tell'
        )
        proc2 = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_set,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()

        # Schedule revert
        if original_profile and original_profile != "Ocean":
            asyncio.get_event_loop().call_later(
                revert_after,
                lambda: asyncio.ensure_future(_revert_terminal_theme(original_profile))
            )
    except Exception:
        pass


async def _revert_terminal_theme(profile_name: str):
    """Revert a Terminal window back to its original profile."""
    escaped = profile_name.replace('"', '\\"')
    script = (
        'tell application "Terminal"\n'
        f'    set current settings of front window to settings set "{escaped}"\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass


async def open_terminal(command: str = "") -> dict:
    """Open Terminal.app and optionally run a command. Marks it blue for VALET."""
    if command:
        escaped = command.replace('"', '\\"')
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "{escaped}"\n'
            "end tell"
        )
    else:
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            "end tell"
        )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_terminal failed: {stderr.decode()}")
    else:
        await _mark_terminal_as_valet()
    return {
        "success": success,
        "confirmation": "Terminal is open, sir." if success else "I had trouble opening Terminal, sir.",
    }


async def delete_file(path: str) -> dict:
    """Move a file to the Trash via Finder. Safer than `rm` — recoverable."""
    raw = path.strip()
    if not raw:
        return {"success": False, "confirmation": "I didn't catch which file, sir."}

    expanded = str(Path(raw).expanduser())
    if not os.path.exists(expanded):
        return {"success": False, "confirmation": f"I can't find {raw}, sir."}

    # Use Finder's "delete" — moves to Trash, not permanent removal.
    escaped = expanded.replace('"', '\\"')
    script = f'tell application "Finder" to delete (POSIX file "{escaped}" as alias)'
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"delete_file '{expanded}' failed: {stderr.decode()}")
    name = os.path.basename(expanded)
    return {
        "success": success,
        "confirmation": f"Moved {name} to the Trash, sir." if success else f"I couldn't trash {name}, sir.",
    }


async def type_into_app(target: str, press_enter: bool = False, task_id: str | None = None) -> dict:
    """Activate an app (if specified) and type text into it via System Events.

    Target format: "AppName ||| text to type"  or  "text to type" (uses current app).
    If press_enter is True, presses Return after typing (sends the message / runs the command).
    If `task_id` is given, a text_write event is emitted to the process panel
    reflecting the typed (or sent) text.
    """
    raw = target.strip()
    if "|||" in raw:
        app, _, text = raw.partition("|||")
        app = app.strip()
        text = text.strip()
    else:
        app = ""
        text = raw

    if not text:
        if task_id:
            await emit_error(task_id, "Type: missing text")
        return {"success": False, "confirmation": "I didn't catch what to type, sir."}

    # Defensive reroute: when the LLM picks "Claude Code" / "Claude" as the
    # target app, that's not a macOS app — it's a CLI running inside Cursor's
    # integrated terminal. AppleScript `tell application "Claude Code"` fails
    # silently and the text vanishes. Redirect to paste_into_cursor_claude
    # which knows how to land it in the right Cursor window.
    if app.lower() in ("claude code", "claude", "cursor"):
        log.info(
            "type_into_app: rerouting target=%r → paste_into_cursor_claude (text_len=%d)",
            app, len(text),
        )
        result = await paste_into_cursor_claude(text, task_id=task_id)
        if result.get("success"):
            return {"success": True, "confirmation": "Sent to Claude Code, sir."}
        reason = result.get("reason", "unknown")
        return {
            "success": False,
            "confirmation": f"Couldn't reach the Claude pane, sir ({reason}).",
        }

    # Activate the target app first so keystrokes land in the right place.
    if app:
        activate_script = f'tell application "{app}" to activate'
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", activate_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {
                "success": False,
                "confirmation": f"I couldn't bring {app} to the front, sir.",
            }
        await asyncio.sleep(0.35)  # let macOS settle focus before keystroke

    # Escape for AppleScript string literal: backslashes first, then quotes.
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    keystroke_script = f'tell application "System Events" to keystroke "{escaped}"'
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", keystroke_script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode().strip()
        log.error(f"type_into_app keystroke failed: {err}")
        # Most common failure: accessibility permission missing.
        if "1002" in err or "not authorized" in err.lower():
            return {
                "success": False,
                "confirmation": "I need Accessibility permission to type, sir. Open System Settings → Privacy & Security → Accessibility and enable the VALET process.",
            }
        return {"success": False, "confirmation": f"Couldn't type that, sir: {err[:120]}"}

    if press_enter:
        await asyncio.sleep(0.15)
        enter_script = 'tell application "System Events" to key code 36'
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", enter_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    where = app if app else "the active app"
    verb = "Sent" if press_enter else "Typed"
    if task_id:
        await emit_text_write(task_id, app, text, sent=press_enter)
    return {"success": True, "confirmation": f"{verb} in {where}, sir."}


async def compose_text_message(recipient: str, body: str,
                               task_id: str | None = None) -> dict:
    """Open Messages, start a NEW message addressed to `recipient`, type `body` —
    and STOP. Never presses send.

    This is the safe replacement for blindly typing into whatever Messages thread
    happens to be focused (the bug that sent a text to the wrong contact). We open
    a fresh compose window (⌘N), type the recipient name, accept the top contact
    suggestion (Return → addresses the message and moves focus to the body field),
    then type the body. The user reviews the recipient chip and presses send
    themselves — VALET deliberately does not, because an autocomplete mismatch
    must never auto-send to the wrong person.
    """
    recipient = (recipient or "").strip()
    body = (body or "").strip()
    if not recipient:
        if task_id:
            await emit_error(task_id, "Compose: missing recipient")
        return {"success": False, "confirmation": "Who should I text, sir?"}
    if not body:
        if task_id:
            await emit_error(task_id, "Compose: missing message")
        return {"success": False, "confirmation": "What should the message say, sir?"}

    # AppleScript string-literal escaping: backslashes first, then quotes.
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    r_esc = _esc(recipient)

    # Save the user's clipboard so we can restore it after pasting the body.
    saved_clip = ""
    try:
        pb = await asyncio.create_subprocess_exec(
            "pbpaste", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await pb.communicate()
        saved_clip = out.decode(errors="replace")
    except Exception:
        pass
    # Put the body on the clipboard (atomic paste is far more reliable than a
    # per-character keystroke, which got dropped mid conversation-switch and left
    # the field empty). pbcopy via stdin so the body can be any length/charset.
    try:
        cp = await asyncio.create_subprocess_exec(
            "pbcopy", stdin=asyncio.subprocess.PIPE)
        await cp.communicate(body.encode())
    except Exception as e:
        log.error(f"compose_text_message pbcopy failed: {e}")
        return {"success": False, "confirmation": "Couldn't prepare that text, sir."}

    # Sequence (each step needs a settle or the next keystroke is dropped/misrouted):
    #   ⌘N            new compose, focus in the To field
    #   type name     recipient autocomplete populates
    #   key 36 Return accept the top suggestion → recipient becomes a chip. Focus
    #                 STAYS in the To field (ready for more recipients) — it does
    #                 NOT drop to the body. Pasting here is what put the text in the
    #                 recipients bar.
    #   key 48 Tab    advance To → message body field
    #   ⌘V            paste the body atomically (NOT sent — we never press Return
    #                 in the body, so it's composed and left for the user to send)
    script = (
        'tell application "Messages" to activate\n'
        'delay 0.7\n'
        'tell application "System Events"\n'
        '  keystroke "n" using {command down}\n'   # new message
        '  delay 0.9\n'
        f'  keystroke "{r_esc}"\n'                  # type recipient name
        '  delay 1.3\n'                             # let autocomplete populate
        '  key code 36\n'                           # Return: accept top suggestion → recipient chip
        '  delay 0.7\n'                             # let the dropdown close / chip commit
        '  key code 48\n'                           # Tab: move To → message body
        '  delay 0.7\n'                             # let focus land in the body
        '  keystroke "v" using {command down}\n'   # paste the body (atomic, NOT sent)
        '  delay 0.3\n'
        'end tell\n'
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    # Restore the user's clipboard (after the paste has landed).
    try:
        await asyncio.sleep(0.4)
        rp = await asyncio.create_subprocess_exec("pbcopy", stdin=asyncio.subprocess.PIPE)
        await rp.communicate(saved_clip.encode())
    except Exception:
        pass

    if proc.returncode != 0:
        err = stderr.decode().strip()
        log.error(f"compose_text_message failed: {err}")
        if "1002" in err or "not authorized" in err.lower():
            return {
                "success": False,
                "confirmation": "I need Accessibility permission to compose a text, sir. Open System Settings → Privacy & Security → Accessibility and enable the VALET process.",
            }
        return {"success": False, "confirmation": f"Couldn't compose that text, sir: {err[:120]}"}

    if task_id:
        await emit_text_write(task_id, "Messages", f"To {recipient}: {body}", sent=False)
    return {
        "success": True,
        "confirmation": f"Composed a text to {recipient}, sir — check the recipient and press send.",
    }


# ── Apple Clock app control (System Events GUI scripting) ───────────────────
# The Clock app has no AppleScript dictionary, so we drive it by clicking named
# AX controls. The tabs are an AXTabGroup of radio buttons (or toolbar buttons on
# older macOS) titled "World Clock"/"Alarms"/"Stopwatch"/"Timers"; Start/Stop/Lap
# are AXButtons titled exactly that. Clicking BY NAME is deterministic — the
# vision loop mis-clicked the screen instead.

_CLOCK_TAB_NAMES = {
    "world clock": "World Clock", "world": "World Clock", "clock": "World Clock",
    "alarms": "Alarms", "alarm": "Alarms",
    "stopwatch": "Stopwatch", "stop watch": "Stopwatch",
    "timers": "Timers", "timer": "Timers",
}


def _clock_canonical_tab(s: str) -> str | None:
    return _CLOCK_TAB_NAMES.get((s or "").strip().lower())


def _clock_switch_snippet(tab: str) -> str:
    """AppleScript that switches the Clock window to the named tab (tab group
    radio button first, toolbar button as a fallback for older macOS)."""
    return (
        '  try\n'
        f'    click (first radio button of tab group 1 of window 1 whose name is "{tab}")\n'
        '  on error\n'
        '    try\n'
        f'      click (first button of toolbar 1 of window 1 whose name is "{tab}")\n'
        '    on error\n'
        f'      try\n        click (first radio button of window 1 whose name is "{tab}")\n      end try\n'
        '    end try\n'
        '  end try\n'
        '  delay 0.5\n'
    )


def _clock_click_button_snippet(btn: str) -> str:
    """AppleScript that clicks an AXButton titled `btn` anywhere in the window."""
    return (
        '  try\n'
        f'    click (first button of window 1 whose name is "{btn}")\n'
        '  on error\n'
        f'    click (first button of (entire contents of window 1) whose name is "{btn}")\n'
        '  end try\n'
    )


def _clock_wrap(steps: str) -> str:
    return (
        'tell application "Clock" to activate\n'
        'delay 0.7\n'
        'tell application "System Events" to tell process "Clock"\n'
        '  set frontmost to true\n'
        '  delay 0.2\n'
        f'{steps}'
        'end tell\n'
    )


async def _run_clock_script(script: str, fail_msg: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode().strip()
        log.error(f"clock script failed: {err}")
        if "1002" in err or "not authorized" in err.lower():
            return {"success": False,
                    "confirmation": "I need Accessibility permission to drive the Clock app, sir."}
        return {"success": False, "confirmation": fail_msg}
    return {"success": True, "confirmation": ""}


async def clock_stopwatch(mode: str) -> dict:
    """Start or stop the Stopwatch in Apple's Clock app — deterministically."""
    want_start = mode in ("start", "begin")
    btn = "Start" if want_start else "Stop"
    script = _clock_wrap(
        _clock_switch_snippet("Stopwatch") + '  delay 0.2\n' + _clock_click_button_snippet(btn))
    res = await _run_clock_script(script, f"Couldn't reach the Stopwatch {btn} button, sir.")
    if res["success"]:
        res["confirmation"] = f"Stopwatch {'started' if want_start else 'stopped'}, sir."
    return res


async def clock_switch_tab(tab_name: str) -> dict:
    """Switch the Clock app to a named tab (World Clock / Alarms / Stopwatch / Timers)."""
    tab = _clock_canonical_tab(tab_name)
    if not tab:
        return {"success": False, "confirmation": f"I don't know a '{tab_name}' tab in Clock, sir."}
    script = _clock_wrap(_clock_switch_snippet(tab))
    res = await _run_clock_script(script, f"Couldn't switch to the {tab} tab, sir.")
    if res["success"]:
        res["confirmation"] = f"Switched to {tab}, sir."
    return res


async def clock_set_timer(seconds: int, label: str = "") -> dict:
    """Set and start a timer in the Clock app's Timers tab.

    Switches to Timers, types the duration as HHMMSS digits (the field fills
    right-aligned), then clicks Start. Best-effort: the Timers digit entry varies
    by macOS version, so this may need a tweak after testing.
    """
    seconds = max(1, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    digits = f"{h:02d}{m:02d}{s:02d}"   # HHMMSS, e.g. 3 min → "000300"
    steps = (
        _clock_switch_snippet("Timers")
        + '  delay 0.4\n'
        + f'  keystroke "{digits}"\n'    # fills the duration field right-aligned
        + '  delay 0.4\n'
        + _clock_click_button_snippet("Start")
    )
    res = await _run_clock_script(_clock_wrap(steps), "Couldn't set the timer in Clock, sir.")
    if res["success"]:
        res["confirmation"] = f"Timer set for {label or f'{seconds} seconds'}, sir."
    return res


async def clock_add_alarm(hour_24: int, minute: int, label: str = "") -> dict:
    """Add an alarm in the Clock app's Alarms tab (best-effort).

    Switches to Alarms, clicks Add (+), types the time, and saves. The add-alarm
    sheet's controls are version-specific, so this is the least certain of the
    Clock actions and may need adjustment after testing.
    """
    hour_24 = max(0, min(23, int(hour_24)))
    minute = max(0, min(59, int(minute)))
    hr12 = hour_24 % 12 or 12
    ampm = "AM" if hour_24 < 12 else "PM"
    time_digits = f"{hr12:02d}{minute:02d}"
    steps = (
        _clock_switch_snippet("Alarms")
        + '  delay 0.4\n'
        + '  try\n'
          '    click (first button of window 1 whose name is "Add")\n'
          '  on error\n'
          '    try\n      click (first button of window 1 whose description is "Add")\n    end try\n'
          '  end try\n'
        + '  delay 0.6\n'
        + f'  keystroke "{time_digits}"\n'
        + f'  keystroke "{ampm}"\n'
        + '  delay 0.3\n'
        + '  try\n'
          '    click (first button of sheet 1 of window 1 whose name is "Save")\n'
          '  on error\n'
          '    try\n      keystroke return\n    end try\n'
          '  end try\n'
    )
    res = await _run_clock_script(_clock_wrap(steps), "Couldn't set the alarm in Clock, sir.")
    if res["success"]:
        res["confirmation"] = f"Alarm set for {hr12}:{minute:02d} {ampm}, sir."
    return res


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".tiff", ".tif", ".bmp", ".heic", ".webp"}


def copy_file_to_clipboard(path: str) -> dict:
    """Put a file on the clipboard so the user can ⌘V it.

    For an image, the IMAGE DATA goes on the pasteboard (so it pastes into docs,
    chats, image fields). For anything else, the file URL goes on the pasteboard
    (so it pastes as a file in Finder/Mail). Uses NSPasteboard directly — there's
    no CLI equivalent for image data (pbcopy is text-only).
    """
    import os
    if not path or not os.path.exists(path):
        return {"success": False, "confirmation": "I couldn't find that file, sir."}
    name = os.path.basename(path)
    try:
        from AppKit import NSPasteboard, NSImage          # pyobjc (already used for AX)
        from Foundation import NSURL
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_EXTS:
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img is not None and pb.writeObjects_([img]):
                return {"success": True, "confirmation": f"Copied {name} to your clipboard, sir."}
            # Fall through to URL copy if the image couldn't be decoded.
        url = NSURL.fileURLWithPath_(path)
        if pb.writeObjects_([url]):
            return {"success": True, "confirmation": f"Copied {name} to your clipboard, sir."}
        return {"success": False, "confirmation": f"Couldn't copy {name} to the clipboard, sir."}
    except Exception as e:
        log.error(f"copy_file_to_clipboard failed: {e}")
        return {"success": False, "confirmation": f"Couldn't copy {name}, sir."}


async def compose_email(to: str, subject: str, body: str) -> dict:
    """Open a pre-filled Gmail compose in the browser — and STOP (never sends).

    Uses the Gmail compose URL (view=cm), which opens a compose window with To,
    Subject and Body filled in, leaving the user to review and press Send. This
    needs no Gmail API (the deferred integration) — just a browser logged into
    Gmail. Compose-and-stop, like the Messages flow.
    """
    from urllib.parse import quote
    to = (to or "").strip()
    url = (
        "https://mail.google.com/mail/?view=cm&fs=1"
        f"&to={quote(to)}&su={quote((subject or '').strip())}&body={quote((body or '').strip())}"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "open", url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.wait()
    except Exception as e:
        log.error(f"compose_email failed: {e}")
        return {"success": False, "confirmation": "Couldn't open the email draft, sir."}
    who = to or "your recipient"
    return {"success": True,
            "confirmation": f"Drafted an email to {who}, sir — review it and press send."}


async def compose_slack(target: str, body: str) -> dict:
    """Open a Slack DM/channel and type a message — and STOP (never sends).

    Uses Slack's ⌘K quick switcher to jump to the named person/channel, then
    pastes the message into the (auto-focused) input. We never press Enter, so the
    message is composed and left for the user to send. Compose-and-stop.
    """
    target = (target or "").strip()
    body = (body or "").strip()
    if not target or not body:
        return {"success": False, "confirmation": "Who on Slack, and what should it say, sir?"}

    t_esc = target.replace("\\", "\\\\").replace('"', '\\"')
    # Save + set clipboard so we can paste the body atomically, then restore.
    saved_clip = ""
    try:
        pb = await asyncio.create_subprocess_exec(
            "pbpaste", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await pb.communicate()
        saved_clip = out.decode(errors="replace")
    except Exception:
        pass
    try:
        cp = await asyncio.create_subprocess_exec("pbcopy", stdin=asyncio.subprocess.PIPE)
        await cp.communicate(body.encode())
    except Exception as e:
        log.error(f"compose_slack pbcopy failed: {e}")
        return {"success": False, "confirmation": "Couldn't prepare that Slack message, sir."}

    script = (
        'tell application "Slack" to activate\n'
        'delay 0.8\n'
        'tell application "System Events"\n'
        '  keystroke "k" using {command down}\n'   # quick switcher
        '  delay 0.9\n'
        f'  keystroke "{t_esc}"\n'                  # type the person/channel
        '  delay 1.1\n'
        '  key code 36\n'                           # open the top match (DM/channel)
        '  delay 1.2\n'
        '  keystroke "v" using {command down}\n'   # paste body into the focused input (NOT sent)
        '  delay 0.3\n'
        'end tell\n'
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()

    try:
        await asyncio.sleep(0.4)
        rp = await asyncio.create_subprocess_exec("pbcopy", stdin=asyncio.subprocess.PIPE)
        await rp.communicate(saved_clip.encode())
    except Exception:
        pass

    if proc.returncode != 0:
        err = stderr.decode().strip()
        log.error(f"compose_slack failed: {err}")
        if "1002" in err or "not authorized" in err.lower():
            return {"success": False,
                    "confirmation": "I need Accessibility permission to drive Slack, sir."}
        if "Slack" in err and ("not running" in err or "isn't running" in err or "-600" in err):
            return {"success": False, "confirmation": "Slack doesn't seem to be open, sir."}
        return {"success": False, "confirmation": "Couldn't compose that Slack message, sir."}
    return {"success": True,
            "confirmation": f"Slack message to {target} ready, sir — review and press send."}


async def run_applescript(script: str) -> dict:
    """Execute arbitrary AppleScript. Full system control — use sparingly."""
    if not script.strip():
        return {"success": False, "confirmation": "Empty script, sir."}

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"applescript failed: {stderr.decode()}")
        return {"success": False, "confirmation": f"AppleScript error: {stderr.decode().strip()[:120]}"}
    out = stdout.decode().strip()
    return {
        "success": True,
        "confirmation": "Done, sir." + (f" Result: {out}" if out else ""),
        "output": out,
    }


# Stack-aware scaffold templates — kept simple so they read in a glance.
# Each entry: (gitignore_text, manifest_filename_or_None, manifest_text_template).
# Template can use {name} substitution for the slug.
_STACK_SCAFFOLDS: dict[str, tuple[str, str | None, str]] = {
    "python": (
        "__pycache__/\n*.pyc\n.venv/\nvenv/\n.env\n.env.*\n*.egg-info/\ndist/\nbuild/\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\n",
        "pyproject.toml",
        '[project]\nname = "{name}"\nversion = "0.1.0"\ndescription = ""\nrequires-python = ">=3.11"\ndependencies = []\n',
    ),
    "node": (
        "node_modules/\ndist/\nbuild/\n.next/\n.env\n.env.*\n.DS_Store\n*.log\ncoverage/\n.vite/\n",
        "package.json",
        '{{\n  "name": "{name}",\n  "version": "0.1.0",\n  "private": true,\n  "scripts": {{\n    "dev": "echo Add your dev command here"\n  }}\n}}\n',
    ),
    "go": (
        "*.exe\n*.test\n*.out\n.env\ndist/\nbin/\nvendor/\n",
        None, "",
    ),
    "rust": (
        "target/\nCargo.lock\n.env\n",
        "Cargo.toml",
        '[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
    ),
    "other": (
        ".DS_Store\n.env\n.env.*\n*.log\nnode_modules/\n__pycache__/\ndist/\nbuild/\n",
        None, "",
    ),
}


async def new_cursor_project(
    name: str,
    base_dir: str | None = None,
    task_id: str | None = None,
    stack: dict | None = None,
) -> dict:
    """Create (or reuse) a project directory and open it in Cursor with `claude` auto-running.

    On a fresh dir: runs `git init`, scaffolds CLAUDE.md (placeholder template)
    and .vscode/tasks.json (auto-runs `claude` in a dedicated terminal panel
    on workspace open via VS Code's runOn=folderOpen pattern), records the
    project alias, opens Cursor full-width, sizes Cursor per layout config.

    No external Terminal.app is spawned — claude lives inside Cursor's
    integrated terminal. First open of each workspace triggers Cursor's
    "Allow Automated Tasks" trust prompt; subsequent opens run silently.

    name: project folder name (kebab-cased internally if necessary)
    base_dir: optional parent (default reads config/design_partner.json#new_project_root, ~/Code)
    task_id: optional process-panel task_id; project.* and app_launch events
        are emitted to the panel when supplied.
    """
    raw_name = name.strip()
    if not raw_name:
        return {"success": False, "confirmation": "I need a project name, sir."}

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-")
    if not slug:
        return {"success": False, "confirmation": "That name doesn't translate to a folder, sir."}

    if base_dir:
        base = Path(base_dir).expanduser()
    else:
        # Always create new projects in the configured new_project_root (default ~/Code/).
        # Voice command "new project for X" never scatters across roots.
        cfg = _design_partner_config()
        base = Path(cfg.get("new_project_root") or "~/Code").expanduser()

    base.mkdir(parents=True, exist_ok=True)
    project_path = base / slug

    if task_id:
        await emit_project_event(task_id, "creating", f"Creating '{slug}'", detail=str(project_path))

    is_new = not project_path.exists()
    if is_new:
        project_path.mkdir(parents=True)
        # git init — best-effort; bare `git` may not be on PATH in some shells.
        try:
            git_proc = await asyncio.create_subprocess_exec(
                "git", "init", "-q",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await git_proc.communicate()
        except Exception as e:
            log.warning(f"git init failed in {project_path}: {e}")

        # Scaffold CLAUDE.md only if absent — never clobber.
        claude_md = project_path / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(_CLAUDE_MD_TEMPLATE.format(name=raw_name))

        # Stack-aware scaffold: .gitignore + optional manifest. Best-effort —
        # picks the "other" template when stack is None or unrecognized.
        lang = ((stack or {}).get("language") or "other").lower()
        gitignore_text, manifest_name, manifest_template = _STACK_SCAFFOLDS.get(
            lang, _STACK_SCAFFOLDS["other"]
        )
        gi_path = project_path / ".gitignore"
        if not gi_path.exists():
            gi_path.write_text(gitignore_text)
        if manifest_name:
            manifest_path = project_path / manifest_name
            if not manifest_path.exists():
                manifest_path.write_text(manifest_template.format(name=slug))

        # Initial commit so the tree is clean when Claude Code's first ship
        # arrives. assert_clean_tree never blocks the very first dispatch.
        try:
            add_proc = await asyncio.create_subprocess_exec(
                "git", "add", "-A",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await add_proc.communicate()
            # `-c` overrides so the commit lands even when global user.* is unset.
            commit_proc = await asyncio.create_subprocess_exec(
                "git",
                "-c", "user.name=VALET",
                "-c", "user.email=valet@local",
                "commit", "-q", "-m", "chore: initial scaffold",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await commit_proc.communicate()
        except Exception as e:
            log.warning(f"initial-scaffold commit failed in {project_path}: {e}")

        if task_id:
            scaffold_detail = "git init + CLAUDE.md + .gitignore"
            if manifest_name:
                scaffold_detail += f" + {manifest_name}"
            scaffold_detail += " + initial commit"
            await emit_project_event(
                task_id, "scaffolding", f"Scaffolded {lang} project",
                detail=scaffold_detail, status="done",
            )
    else:
        log.info(f"new_cursor_project: '{project_path}' already exists, reusing")
        if task_id:
            await emit_step(task_id, "Reusing existing folder", detail=str(project_path))

    # Ensure .vscode/tasks.json exists BEFORE Cursor opens so the runOn=folderOpen
    # task fires on this workspace load. No-clobber if already present.
    wrote_tasks = _ensure_cursor_task(project_path, task_id=task_id)
    if task_id and wrote_tasks:
        await emit_project_event(
            task_id, "scaffolding", "Scaffolded .vscode/tasks.json",
            detail="claude auto-runs on open (first time: trust prompt)",
            status="done",
        )

    # Record / refresh alias so "open <name>" works next time.
    try:
        from memory import record_project
        record_project(raw_name, str(project_path))
        if slug.lower() != raw_name.lower():
            record_project(slug, str(project_path))
    except Exception as e:
        log.warning(f"record_project failed: {e}")

    if task_id:
        await emit_project_event(task_id, "opening_cursor", "Opening Cursor", status="active")

    proc = await asyncio.create_subprocess_exec(
        "open", "-na", "Cursor", "--args", str(project_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Fall back to plain `open -a Cursor <path>` if `-na` syntax misbehaves.
        proc = await asyncio.create_subprocess_exec(
            "open", "-a", "Cursor", str(project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error(f"new_cursor_project open failed: {stderr.decode()}")
            if task_id:
                await emit_error(task_id, "Cursor failed to open", detail=stderr.decode()[:200])
            return {
                "success": False,
                "confirmation": f"I created {project_path} but couldn't open it in Cursor, sir.",
            }
    if task_id:
        await emit_app_launch(task_id, "Cursor", status="done", detail=str(project_path))

    # Cursor's integrated terminal runs `claude` via .vscode/tasks.json — no
    # external Terminal.app spawn. Just size Cursor to the configured width.
    asyncio.create_task(_position_cursor())

    # Warm-context loader runs in parallel — its own task_context narrates progress.
    asyncio.create_task(_load_warm_context(project_path))

    if task_id:
        await emit_project_event(
            task_id, "ready", "Project ready",
            detail=str(project_path), status="done",
        )

    verb = "New project" if is_new else f"{raw_name} reopened"
    return {
        "success": True,
        "confirmation": f"{verb} at {project_path}, sir." if is_new else f"{verb}, sir.",
        "path": str(project_path),
    }


async def _set_window_bounds(
    process_name: str,
    x_pct: float,
    width_pct: float,
    y_pct: float = 0.0,
    height_pct: float = 1.0,
) -> bool:
    """Position the front window of `process_name` to a rectangular slice of the main display.

    Uses System Events (not the app's native scripting dictionary, which most
    apps don't expose) so it works for Cursor, Terminal, and most third-party
    GUI apps. Best-effort — silently returns False if the app has no window
    or Accessibility permission is missing.
    """
    script = f'''
    tell application "Finder"
        set screenBounds to bounds of window of desktop
    end tell
    set screenW to item 3 of screenBounds
    set screenH to item 4 of screenBounds
    set xPos to (screenW * {x_pct}) as integer
    set yPos to (screenH * {y_pct}) as integer
    set winW to (screenW * {width_pct}) as integer
    set winH to (screenH * {height_pct}) as integer
    tell application "System Events"
        tell process "{process_name}"
            try
                set position of front window to {{xPos, yPos}}
                set size of front window to {{winW, winH}}
            end try
        end tell
    end tell
    '''
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(f"_set_window_bounds({process_name}) failed: {stderr.decode()[:200]}")
        return False
    return True


async def _position_cursor() -> None:
    """Wait for Cursor to be ready, then size its front window per config.

    Default config gives Cursor the top-left quarter of the screen for better
    workspace organization. Layout overrides in
    config/design_partner.json#window_layout.cursor still apply.

    Cursor needs ~1.8s after launch before its window can be targeted via
    System Events; 2s gives a small safety margin.
    """
    cfg = _design_partner_config().get("window_layout", {})
    cursor_cfg = cfg.get(
        "cursor",
        {"x_pct": 0.0, "y_pct": 0.0, "width_pct": 0.5, "height_pct": 0.5},
    )
    await asyncio.sleep(2.0)
    await _set_window_bounds(
        "Cursor",
        cursor_cfg["x_pct"],
        cursor_cfg["width_pct"],
        y_pct=cursor_cfg.get("y_pct", 0.0),
        height_pct=cursor_cfg.get("height_pct", 1.0),
    )


async def _spawn_external_terminal(project_path: Path, task_id: str | None = None) -> dict:
    """Open a real Terminal.app window at `project_path` running `claude`.

    NOT called by the lifecycle flow — claude now runs inside Cursor's
    integrated terminal via .vscode/tasks.json. Kept available for future use
    (e.g. a "give me a real Terminal here" voice command, or fallback when
    Cursor's task runner is disabled).
    """
    term_script = (
        'tell application "Terminal"\n'
        '    activate\n'
        f'    do script "cd {project_path} && claude --dangerously-skip-permissions"\n'
        'end tell'
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", term_script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if success:
        await _mark_terminal_as_valet()
        if task_id:
            await emit_app_launch(task_id, "Terminal", status="done", detail=str(project_path))
    else:
        log.warning(f"_spawn_external_terminal failed: {stderr.decode()[:200]}")
        if task_id:
            await emit_error(task_id, "Terminal launch failed", detail=stderr.decode()[:200])
    return {"success": success}


async def open_project(slug_or_alias: str, task_id: str | None = None) -> dict:
    """Open an existing project (Cursor + side-by-side Terminal) without scaffolding.

    Resolution order:
      1. project_aliases table (exact, case-insensitive)
      2. Exact directory match in ~/Code/ then ~/projects/
      3. Fuzzy substring match across ~/Code/ + ~/projects/

    If fuzzy matching returns >1 candidate, refuses and asks for specificity.
    """
    name = slug_or_alias.strip()
    if not name:
        return {"success": False, "confirmation": "Which project, sir?"}

    if task_id:
        await emit_project_event(task_id, "opening", f"Opening '{name}'", status="active")

    # 1. Alias table (with auto-repair on stale rows pointing at moved projects)
    from memory import resolve_project, record_project, touch_project, _normalize_project_key, _keys_match, update_alias_path
    resolved = resolve_project(name)
    path: Path | None = None
    stale_alias: dict | None = None

    if resolved:
        rp = Path(resolved)
        if rp.exists():
            path = rp
        else:
            # Alias hit but path is gone. Try silent repair by searching for
            # the same basename in any configured root.
            basename = rp.name
            candidates = [r / basename for r in _project_roots() if (r / basename).exists()]
            if len(candidates) == 1:
                new_path = candidates[0]
                update_alias_path(name, str(new_path))
                log.info(f"open_project auto-repaired alias '{name}': {resolved} -> {new_path}")
                path = new_path
            else:
                # Note for caller to offer removal (only matters if fs scan also misses)
                stale_alias = {"alias": name, "path": resolved}

    # 2-3. Filesystem fallbacks — scan all configured project_roots. Same
    # normalization (camelcase-split + hyphen-tolerant) on both sides.
    if path is None:
        roots = _project_roots()
        key = _normalize_project_key(name)

        exact_matches: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for entry in root.iterdir():
                if entry.is_dir() and _keys_match(key, _normalize_project_key(entry.name), exact=True):
                    exact_matches.append(entry)
        if len(exact_matches) == 1:
            path = exact_matches[0]
        elif len(exact_matches) > 1:
            locations = ", ".join(str(m) for m in exact_matches[:5])
            if task_id:
                await emit_error(task_id, f"Ambiguous: {len(exact_matches)} '{name}' across roots", detail=locations)
            return {
                "success": False,
                "confirmation": f"I found {len(exact_matches)} projects called '{name}' in different roots, sir — say which: {locations}",
            }

        if path is None:
            candidates: list[Path] = []
            for root in roots:
                if not root.exists():
                    continue
                for entry in root.iterdir():
                    if entry.is_dir() and not entry.name.startswith(".") and _keys_match(key, _normalize_project_key(entry.name), exact=False):
                        candidates.append(entry)
            if len(candidates) == 1:
                path = candidates[0]
            elif len(candidates) > 1:
                names = ", ".join(c.name for c in candidates[:5])
                if task_id:
                    await emit_error(task_id, f"Ambiguous: {len(candidates)} matches", detail=names)
                return {
                    "success": False,
                    "confirmation": f"I found {len(candidates)} projects matching '{name}', sir — try more of the name.",
                }

    if path is None:
        # No match at all — return suggestions so the caller can offer to
        # register an alias on the spot. Stale-alias info bubbles up too.
        suggestions = _fuzzy_suggestions(name, limit=3)
        if task_id:
            await emit_error(task_id, f"No project '{name}'",
                              detail=f"suggestions: {[s['name'] for s in suggestions]}")
        return {
            "success": False,
            "confirmation": f"I couldn't find a project called '{name}', sir.",
            "suggestions": suggestions,
            "stale_alias": stale_alias,
        }

    try:
        record_project(name, str(path))
        touch_project(str(path))
    except Exception as e:
        log.warning(f"record/touch_project failed: {e}")

    # Scaffold .vscode/tasks.json BEFORE Cursor opens (no-clobber). This makes
    # the runOn=folderOpen task fire on this workspace load and gives
    # pre-existing projects the auto-claude behavior for free.
    wrote_tasks = _ensure_cursor_task(path, task_id=task_id)
    if task_id and wrote_tasks:
        await emit_project_event(
            task_id, "scaffolding", "Added .vscode/tasks.json",
            detail="claude auto-runs on open (first time: trust prompt)",
            status="done",
        )

    if task_id:
        await emit_project_event(task_id, "opening_cursor", "Opening Cursor", detail=str(path), status="active")

    proc = await asyncio.create_subprocess_exec(
        "open", "-a", "Cursor", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error(f"open_project Cursor failed: {stderr.decode()}")
        if task_id:
            await emit_error(task_id, "Cursor failed to open", detail=stderr.decode()[:200])
        return {"success": False, "confirmation": f"I couldn't open {path.name} in Cursor, sir."}
    if task_id:
        await emit_app_launch(task_id, "Cursor", status="done", detail=str(path))

    # Size Cursor to the configured width (default full screen). No external
    # Terminal — claude runs inside Cursor via the tasks.json auto-run.
    asyncio.create_task(_position_cursor())
    asyncio.create_task(_load_warm_context(path))

    if task_id:
        await emit_project_event(task_id, "ready", "Project ready", detail=str(path), status="done")

    return {"success": True, "confirmation": f"{path.name} is up, sir.", "path": str(path)}


def list_projects() -> list[dict]:
    """Return all known projects: alias entries (most-recent first) + filesystem scan.

    Scans every root in `config/design_partner.json#project_roots`. Same project
    name in two roots → both entries listed (dedup is by canonical path, not name).

    Shape: [{"name": str, "path": str, "source": "alias"|"fs"}].
    Synchronous — cheap filesystem listdir.
    """
    from memory import list_known_projects
    seen: dict[str, dict] = {}
    for entry in list_known_projects():
        raw = entry["path"]
        if not raw:
            continue
        p = Path(raw)
        if not p.exists():
            continue
        key = str(p.resolve())
        if key not in seen:
            seen[key] = {"name": entry["alias"], "path": str(p), "source": "alias"}

    for root in _project_roots():
        if not root.exists():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                key = str(entry.resolve())
                if key not in seen:
                    seen[key] = {"name": entry.name, "path": str(entry), "source": "fs"}
        except Exception:
            pass
    return list(seen.values())


async def register_project(path: str, alias: str | None = None, task_id: str | None = None) -> dict:
    """Record a path → alias mapping so "open <alias>" resolves to a project
    living outside any configured root.

    `path` may be tilde-prefixed or absolute. The alias defaults to the dir's
    basename if not provided. Idempotent: re-registering an existing alias
    updates the path. Returns {success, confirmation, path, alias}.
    """
    raw = (path or "").strip()
    if not raw:
        return {"success": False, "confirmation": "I need a path, sir."}

    expanded = Path(raw).expanduser().resolve()
    if not expanded.exists():
        if task_id:
            await emit_error(task_id, f"Path missing: {expanded}")
        return {"success": False, "confirmation": f"That path doesn't exist, sir: {expanded}"}
    if not expanded.is_dir():
        if task_id:
            await emit_error(task_id, f"Not a directory: {expanded}")
        return {"success": False, "confirmation": f"That's not a directory, sir: {expanded}"}

    final_alias = (alias or expanded.name).strip()
    if not final_alias:
        return {"success": False, "confirmation": "I need an alias for that project, sir."}

    try:
        from memory import record_project
        record_project(final_alias, str(expanded))
    except Exception as e:
        log.error(f"register_project DB write failed: {e}")
        if task_id:
            await emit_error(task_id, "Alias DB write failed", detail=str(e)[:200])
        return {"success": False, "confirmation": "I couldn't save that alias, sir."}

    if task_id:
        await emit_project_event(
            task_id, "registered", f"Registered '{final_alias}'",
            detail=str(expanded), status="done",
        )

    return {
        "success": True,
        "confirmation": f"Registered '{final_alias}' at {expanded}, sir.",
        "path": str(expanded),
        "alias": final_alias,
    }


async def refresh_calendar_tabs() -> dict:
    """Reload any open Google Calendar tab in Chrome so the user sees fresh data.

    Used after VALET creates/cancels/updates events via the API. The user
    sees the change land in their browser without having to refresh manually.
    Silent no-op if no calendar.google.com tab is open.
    """
    script = '''
    tell application "Google Chrome"
        set reloaded to 0
        try
            repeat with w in windows
                set tabIdx to 0
                repeat with t in tabs of w
                    set tabIdx to tabIdx + 1
                    if (URL of t) starts with "https://calendar.google.com" then
                        tell t to reload
                        set reloaded to reloaded + 1
                    end if
                end repeat
            end repeat
        end try
        return reloaded as string
    end tell
    '''
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    n = stdout.decode().strip() if proc.returncode == 0 else "0"
    return {"success": proc.returncode == 0, "tabs_reloaded": n}


async def open_app_or_path(target: str, task_id: str | None = None) -> dict:
    """Open a macOS app by name or a filesystem path in Finder.

    Routes:
      - "Finder" / "Slack" / "Notes" / "Spotify" → `open -a <App Name>`
      - "/Users/foo/Desktop" / "~/Documents" → expanded then `open <path>`
      - "Desktop" (bare folder name) → opens that subfolder of $HOME if it exists

    If `task_id` is given, an app_launch (or error) event is emitted to the
    process panel reflecting the outcome.
    """
    raw = target.strip()
    if not raw:
        if task_id:
            await emit_error(task_id, "Open: missing target")
        return {"success": False, "confirmation": "I didn't catch what to open, sir."}

    # Path-like → open in Finder
    if raw.startswith("/") or raw.startswith("~") or raw.startswith("./"):
        path = str(Path(raw).expanduser())
        proc = await asyncio.create_subprocess_exec(
            "open", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        success = proc.returncode == 0
        if not success:
            log.error(f"open path '{path}' failed: {stderr.decode()}")
        if task_id:
            if success:
                await emit_app_launch(task_id, path, status="done", detail="Opened in Finder")
            else:
                await emit_error(task_id, f"Couldn't open {path}", detail=stderr.decode()[:200])
        return {
            "success": success,
            "confirmation": f"Opened {path} in Finder, sir." if success else f"I couldn't open {path}, sir.",
        }

    # Bare folder name like "Desktop" or "Downloads" → open under $HOME
    home = os.path.expanduser("~")
    candidate = os.path.join(home, raw)
    if os.path.isdir(candidate):
        proc = await asyncio.create_subprocess_exec(
            "open", candidate,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await proc.communicate()
        if proc.returncode == 0:
            if task_id:
                await emit_app_launch(task_id, raw, status="done", detail=f"Folder: {candidate}")
            return {"success": True, "confirmation": f"Opened your {raw} folder, sir."}

    # Otherwise treat as an app name
    proc = await asyncio.create_subprocess_exec(
        "open", "-a", raw,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open -a '{raw}' failed: {stderr.decode()}")
    if task_id:
        if success:
            await emit_app_launch(task_id, raw, status="done")
        else:
            await emit_error(task_id, f"No app called {raw}", detail=stderr.decode()[:200])
    if success and raw.strip().lower() == "cursor":
        asyncio.create_task(_position_cursor())
    return {
        "success": success,
        "confirmation": f"{raw} is up, sir." if success else f"I couldn't find an app called {raw}, sir.",
    }


async def open_browser(url: str, browser: str = "chrome") -> dict:
    """Open URL in user's browser (Chrome or Firefox).

    Uses macOS `open -a` rather than AppleScript so the URL is routed to the
    main browser app bundle via Launch Services. AppleScript's
    `tell application "Google Chrome"` can misfire when a Chrome PWA window
    (e.g. the VALET app shell) is in the foreground — it targets the PWA's
    Chrome process instead of the real browser. `open -a` always hits the
    actual app bundle.
    """
    app_name = "Firefox" if browser.lower() == "firefox" else "Google Chrome"
    display_name = "Firefox" if app_name == "Firefox" else "Chrome"

    proc = await asyncio.create_subprocess_exec(
        "open", "-a", app_name, url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_browser ({display_name}) failed: {stderr.decode()}")
    return {
        "success": success,
        "confirmation": f"Pulled that up in {display_name}, sir." if success else f"{display_name} ran into a problem, sir.",
    }


# Keep backward compat
async def open_chrome(url: str) -> dict:
    return await open_browser(url, "chrome")


async def open_claude_in_project(project_dir: str, prompt: str) -> dict:
    """Open Terminal, cd to project dir, run Claude Code interactively.

    On a fresh project (no CLAUDE.md), writes the prompt to CLAUDE.md so claude
    picks it up on startup. On a project that already has a CLAUDE.md (the
    warm-context source of truth), writes the per-invocation prompt to a
    sidecar `.valet_prompt.md` instead — never clobbers user-owned context.
    """
    claude_md = Path(project_dir) / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(f"# Task\n\n{prompt}\n\nBuild this completely. If web app, make index.html work standalone.\n")
    else:
        # Preserve existing CLAUDE.md (Phase 2 warm-context loader reads it).
        sidecar = Path(project_dir) / ".valet_prompt.md"
        sidecar.write_text(f"# Latest task\n\n{prompt}\n")

    # Launch claude interactive — it reads CLAUDE.md on its own
    script = (
        'tell application "Terminal"\n'
        "    activate\n"
        f'    do script "cd {project_dir} && claude --dangerously-skip-permissions"\n'
        "end tell"
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_claude_in_project failed: {stderr.decode()}")
    else:
        await _mark_terminal_as_valet()
    return {
        "success": success,
        "confirmation": "Claude Code is running in Terminal, sir. You can watch the progress."
        if success
        else "Had trouble spawning Claude Code, sir.",
    }


async def prompt_existing_terminal(project_name: str, prompt: str) -> dict:
    """Find a Terminal window matching a project name and type a prompt into it.

    Uses System Events keystroke to type into an active Claude Code session
    rather than `do script` which would open a new shell.
    """
    escaped_name = project_name.replace('"', '\\"')
    escaped_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')

    # Single atomic script: find window, focus it, type into it
    script = f'''
tell application "Terminal"
    set matched to false
    set targetWindow to missing value
    repeat with w in windows
        if name of w contains "{escaped_name}" then
            set targetWindow to w
            set matched to true
            exit repeat
        end if
    end repeat

    if not matched then
        return "NOT_FOUND"
    end if

    -- Bring the matched window to front
    set index of targetWindow to 1
    set selected tab of targetWindow to selected tab of targetWindow
    activate
end tell

-- Wait for window to be fully focused
delay 1

-- Now type into it
tell application "System Events"
    tell process "Terminal"
        set frontmost to true
        delay 0.3
        keystroke "{escaped_prompt}"
        delay 0.2
        keystroke return
    end tell
end tell

return "OK"
'''

    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        result = stdout.decode().strip()
        if result == "NOT_FOUND":
            return {
                "success": False,
                "confirmation": f"Couldn't find a terminal for {project_name}, sir.",
            }

        success = proc.returncode == 0
        if not success:
            log.error(f"prompt_existing_terminal failed: {stderr.decode()[:200]}")

        if success:
            await _mark_terminal_as_valet()

        return {
            "success": success,
            "confirmation": f"Sent that to {project_name}, sir." if success
            else f"Had trouble typing into {project_name}, sir.",
        }

    except asyncio.TimeoutError:
        return {"success": False, "confirmation": "Terminal operation timed out, sir."}
    except Exception as e:
        log.error(f"prompt_existing_terminal failed: {e}")
        return {"success": False, "confirmation": "Something went wrong reaching that terminal, sir."}


async def get_chrome_tab_info() -> dict:
    """Read the current Chrome tab's title and URL via AppleScript."""
    script = (
        'tell application "Google Chrome"\n'
        "    set tabTitle to title of active tab of front window\n"
        "    set tabURL to URL of active tab of front window\n"
        '    return tabTitle & "|" & tabURL\n'
        "end tell"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            result = stdout.decode().strip()
            parts = result.split("|", 1)
            if len(parts) == 2:
                return {"title": parts[0], "url": parts[1]}
        return {}
    except Exception as e:
        log.warning(f"get_chrome_tab_info failed: {e}")
        return {}


async def _frontmost_app_name() -> str:
    """Return the name of the macOS frontmost app, or "" on any failure.

    Used by paste_into_cursor_claude as a pre-flight gate so we don't fire
    keystrokes into whatever app happens to have focus.
    """
    script = (
        'tell application "System Events"\n'
        '    set frontApp to name of first application process whose frontmost is true\n'
        'end tell\n'
        'return frontApp'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception as e:
        log.debug(f"_frontmost_app_name failed: {e}")
    return ""


async def _focused_signature() -> tuple[str, str]:
    """(AXRole, AXDescription) of the frontmost app's focused UI element.

    Both "" if unreadable. Verified against this Cursor build (read-only AX
    probe): the integrated terminal's focused element is an *AXTextField*
    (NOT AXTextArea — that earlier assumption was the ship bug) whose
    AXDescription carries the terminal tab name plus xterm's screen-reader
    hint, e.g.:
        "Terminal 2, Start Claude … Use ⌥F1 for terminal accessibility help"
    The command palette, editor, and other inputs are also AXTextField but
    their AXDescription does NOT contain "terminal" — so the description, not
    the role, is the reliable discriminator.
    """
    probe = (
        'tell application "System Events"\n'
        '    try\n'
        '        set p to first application process whose frontmost is true\n'
        '        set el to value of attribute "AXFocusedUIElement" of p\n'
        '        set r to ""\n'
        '        set d to ""\n'
        '        try\n'
        '            set r to value of attribute "AXRole" of el\n'
        '        end try\n'
        '        try\n'
        '            set d to value of attribute "AXDescription" of el\n'
        '        end try\n'
        '        return r & "\t" & d\n'
        '    on error\n'
        '        return "\t"\n'
        '    end try\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", probe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        role, _, desc = out.decode().partition("\t")
        return role.strip(), desc.strip()
    except Exception as e:
        log.debug("_focused_signature: probe failed: %s", e)
        return "", ""


def _is_terminal_focus(role: str, desc: str) -> bool:
    """True when the focused element is a Cursor integrated-terminal pane.

    The terminal's AXDescription always contains "terminal" (tab label +
    xterm's "…terminal accessibility help" hint). The palette/editor/search
    inputs don't, so this cleanly separates "safe to paste" from everything
    else regardless of the AXRole (which is AXTextField for the terminal here).
    """
    return "terminal" in (desc or "").lower()


async def _toggle_terminal() -> None:
    """Ctrl+` — VS Code/Cursor's toggle-terminal. Focuses a visible-but-
    unfocused terminal (and reveals a hidden one). We only fire it when the
    terminal isn't already focused, so its hide-if-focused behavior never bites.
    Used instead of the "Terminal: Focus Terminal" palette command, which
    misbehaves in this Cursor build (spawns a fresh terminal / leaves the
    palette open)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e",
            'tell application "System Events" to key code 50 using {control down}',  # 50 = `
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except Exception as e:
        log.debug("_toggle_terminal failed: %s", e)


async def _focus_cursor_terminal() -> str:
    """Ensure Cursor's integrated terminal (where the Claude session runs) holds
    keyboard focus before we paste. Returns "terminal" if confirmed focused,
    "unknown" otherwise (caller decides whether to risk a best-effort paste).

    Strategy (no command palette — it's unreliable in this Cursor build):
      1. Probe the focused element. If it's already a terminal, done — no
         keystroke needed (this is the common case: the user is looking at the
         Claude pane when they ship).
      2. Otherwise Ctrl+` to focus the terminal, re-probe. A second tap covers
         the case where the first one toggled a focused terminal off.
    """
    role, desc = await _focused_signature()
    log.info("_focus_cursor_terminal: pre role=%r desc=%r", role, desc[:140])
    if _is_terminal_focus(role, desc):
        return "terminal"

    for attempt in range(2):
        await _toggle_terminal()
        await asyncio.sleep(0.25)
        role, desc = await _focused_signature()
        log.info("_focus_cursor_terminal: post-toggle[%d] role=%r desc=%r", attempt, role, desc[:140])
        if _is_terminal_focus(role, desc):
            return "terminal"

    return "unknown"


# Foreground-process basenames that mean a pane is sitting at a *shell* prompt
# (Claude not running / already exited) rather than inside the Claude TUI.
_SHELL_COMMS = {"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh"}


def _cmd_basename(command: str) -> str:
    """Lowercased basename of a `ps` command string's argv[0], leading login
    dash stripped (`-zsh` → `zsh`). Empty string if unparseable."""
    if not command:
        return ""
    arg0 = command.split()[0]
    base = arg0.rsplit("/", 1)[-1]
    if base.startswith("-"):
        base = base[1:]
    return base.lower()


async def _cursor_terminal_pane_summary() -> dict:
    """Classify every Cursor integrated-terminal pane by its foreground process.

    Why this exists: the paste lands in whichever integrated terminal currently
    holds keyboard focus, and AppleScript can't tell us which tty that is. But
    every integrated terminal is a descendant of the single
    "Cursor Helper: terminal pty-host" process, each on its own tty, and the
    process table DOES tell us each tty's foreground program. So we read the
    whole set:

      - If every Cursor pane is running `claude` and none is a bare shell, then
        whatever pane is focused MUST be a Claude pane → safe to paste.
      - If any pane is a shell (Claude exited, or a fresh terminal was just
        spawned — the reported bug), we can't guarantee the focused pane is
        Claude, so the caller falls back to file-ship instead of dumping the
        prompt at a `%` prompt.

    Returns {"claude": int, "shell": int, "other": int, "ttys": {tty: kind}}.
    On any failure returns all-zero counts with "error" set, and the caller
    treats "can't tell" as "don't auto-paste".

    A pane is classified by its FOREGROUND process group (the `+` flag in
    `ps` stat): claude > shell > other. The intermediate login shell that
    spawned claude is in the same process group but backgrounded, so a live
    Claude pane reads as "claude", and only reverts to "shell" once claude
    actually exits and the shell returns to the foreground.
    """
    summary = {"claude": 0, "shell": 0, "other": 0, "ttys": {}, "error": None}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps", "-axww", "-o", "pid=,ppid=,tty=,stat=,command=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except Exception as e:
        log.warning("_cursor_terminal_pane_summary: ps failed: %s", e)
        summary["error"] = str(e)[:120]
        return summary

    rows = []           # (pid, ppid, tty, stat, command)
    by_pid = {}         # pid -> row
    children = {}       # ppid -> [pid, ...]
    for line in out.decode(errors="replace").splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        tty, stat, command = parts[2], parts[3], parts[4]
        row = (pid, ppid, tty, stat, command)
        rows.append(row)
        by_pid[pid] = row
        children.setdefault(ppid, []).append(pid)

    # Find the Cursor terminal pty-host(s) and gather all descendants.
    host_pids = [r[0] for r in rows if "pty-host" in r[4] and "cursor" in r[4].lower()]
    if not host_pids:
        log.info("_cursor_terminal_pane_summary: no Cursor pty-host found")
        return summary  # no Cursor terminals → all zero; caller falls back

    descendants = []
    stack = list(host_pids)
    seen = set()
    while stack:
        pid = stack.pop()
        for child in children.get(pid, ()):
            if child in seen:
                continue
            seen.add(child)
            descendants.append(by_pid[child])
            stack.append(child)

    # Group foreground processes by tty. A pane's kind is decided by the
    # highest-priority foreground process on its tty: claude > shell > other.
    PRIORITY = {"claude": 3, "shell": 2, "other": 1}
    tty_kind = {}
    for pid, ppid, tty, stat, command in descendants:
        if not tty or tty in ("??", "-"):
            continue
        if "+" not in stat:            # only the foreground process group counts
            continue
        base = _cmd_basename(command)
        if base == "claude":
            kind = "claude"
        elif base in _SHELL_COMMS:
            kind = "shell"
        else:
            kind = "other"
        if PRIORITY[kind] > PRIORITY.get(tty_kind.get(tty, "other"), 0):
            tty_kind[tty] = kind

    for tty, kind in tty_kind.items():
        summary[kind] += 1
    summary["ttys"] = tty_kind
    log.info(
        "_cursor_terminal_pane_summary: claude=%d shell=%d other=%d ttys=%s",
        summary["claude"], summary["shell"], summary["other"], tty_kind,
    )
    return summary


async def _list_cursor_window_names() -> list[str]:
    """All Cursor window titles, one per line. Empty list on any AppleScript
    error. Window names look like "<file> — <project-name>" or just
    "<project-name>" when no editor tab is active.
    """
    script = (
        'tell application "System Events"\n'
        '    tell process "Cursor"\n'
        '        try\n'
        '            set out to ""\n'
        '            repeat with w in (every window)\n'
        '                set out to out & (name of w) & linefeed\n'
        '            end repeat\n'
        '            return out\n'
        '        on error\n'
        '            return ""\n'
        '        end try\n'
        '    end tell\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode != 0:
            return []
        names = [n.strip() for n in stdout.decode().splitlines() if n.strip()]
        return names
    except Exception as e:
        log.debug(f"_list_cursor_window_names failed: {e}")
        return []


def _match_cursor_window(window_names: list[str], project_basename: str) -> str | None:
    """Pick the window whose name best identifies the target project.

    Strategy: case-insensitive match against the project basename. Prefer
    endswith (Cursor's "<file> — <project>" pattern), fall back to substring
    contains. Returns the window name verbatim (so AppleScript can match it
    back), or None if no candidate matched.
    """
    needle = project_basename.lower().strip()
    if not needle:
        return None
    # Pass 1: window names that end with the project basename (covers
    # both "<file> — <project>" and bare "<project>" titles).
    for name in window_names:
        if name.lower().endswith(needle):
            return name
    # Pass 2: substring contains.
    for name in window_names:
        if needle in name.lower():
            return name
    return None


async def _raise_cursor_window(window_name: str) -> bool:
    """Bring the named Cursor window to the front via AXRaise.

    AppleScript-quotes the window name (doubles internal quotes), then
    issues `perform action "AXRaise"`. Returns True on success.
    """
    safe = window_name.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "System Events"\n'
        '    tell process "Cursor"\n'
        '        try\n'
        f'            perform action "AXRaise" of (first window whose name is "{safe}")\n'
        '            return "ok"\n'
        '        on error errMsg\n'
        '            return "err:" & errMsg\n'
        '        end try\n'
        '    end tell\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        out = stdout.decode().strip()
        ok = out == "ok"
        if not ok:
            log.warning("_raise_cursor_window(%r) failed: %s", window_name, out[:160])
        return ok
    except Exception as e:
        log.debug(f"_raise_cursor_window exception: {e}")
        return False


async def paste_into_cursor_claude(
    prompt: str,
    task_id: str | None = None,
    target_project_path: str | None = None,
    restore_focus: bool = False,
) -> dict:
    """Auto-paste a prompt into Cursor's active claude terminal pane.

    Used by both design ship-it (chunk 21 Mode 1) and dictation
    (Mode 2). The flow:

      1. Pre-flight: confirm Cursor is the macOS frontmost app. If not,
         we DO NOT attempt to bring it forward — the spec is that the
         user has Cursor open and focused with the claude pane visible.
         If Cursor isn't focused, return {"success": False, "reason":
         "cursor_not_focused", "frontmost": <whatever>} so the caller
         can write the prompt to .valet/inbox/<task_id>.md as a fallback.
      2. Save the user's existing clipboard via `pbpaste` so we can
         restore it after the paste lands. We don't want to clobber
         whatever they had copied.
      3. Put the prompt on the clipboard via `pbcopy`.
      4. Send Cmd+V then Return via osascript / System Events. Small
         pause between activate and keystroke gives the input focus a
         beat to land in the integrated terminal.
      5. Restore the saved clipboard after a short delay (long enough
         that the paste has consumed the prompt).

    Every step has a tight timeout so a hung osascript can't block the
    ship-it flow indefinitely. Returns:
      {"success": True}                               — paste fired cleanly
      {"success": False, "reason": "...", "detail": ".."}  — caller falls back
    Never raises. All telemetry goes to logs/valet.err.log via log.
    """
    # ── (0) Optional window targeting. If a project path is supplied, look
    # for a Cursor window whose title identifies that project and AXRaise
    # it BEFORE activating Cursor — so when activation flips Cursor to the
    # front, the correct window (and its claude pane) is the one we paste
    # into. Falls back gracefully on no-match.
    if target_project_path:
        project_basename = Path(target_project_path).name
        window_names = await _list_cursor_window_names()
        matched = _match_cursor_window(window_names, project_basename)
        log.info(
            "paste_into_cursor_claude: project=%r → matched window=%r (from %d candidates)",
            project_basename, matched, len(window_names),
        )
        if matched:
            await _raise_cursor_window(matched)
            # Brief beat so the raise takes effect before activate fires.
            await asyncio.sleep(0.15)

    # ── (1) Pre-flight: bring Cursor forward if it isn't already. ──────
    # The typical caller is the design ship-it button OR a voice "ship it"
    # spoken to the VALET browser tab — in both cases Chrome (not Cursor)
    # is frontmost. Rather than refuse, we activate Cursor and re-check.
    # Only fail if Cursor.app isn't installed/launchable.
    # Save the original frontmost so we can return focus after pasting
    # ("stealth paste") — user is reading VALET in Chrome and shouldn't
    # be yanked into Cursor every time they ship.
    original_frontmost = await _frontmost_app_name()
    frontmost = original_frontmost
    log.info("paste_into_cursor_claude: frontmost=%r (will activate Cursor if not already)", frontmost)
    if frontmost.lower() != "cursor":
        try:
            activate = await asyncio.create_subprocess_exec(
                "osascript", "-e", 'tell application "Cursor" to activate',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, activate_err = await asyncio.wait_for(activate.communicate(), timeout=3.0)
            if activate.returncode != 0:
                log.warning("paste_into_cursor_claude: Cursor activate failed: %s", activate_err.decode()[:160])
                return {
                    "success": False,
                    "reason": "cursor_unavailable",
                    "frontmost": frontmost,
                    "detail": f"Could not activate Cursor.app — is it installed/running? "
                               f"AppleScript said: {activate_err.decode()[:120]}",
                }
        except Exception as e:
            log.warning("paste_into_cursor_claude: activate exception: %s", e)
            return {
                "success": False,
                "reason": "cursor_unavailable",
                "frontmost": frontmost,
                "detail": str(e)[:200],
            }
        # Give the WM a beat to actually focus Cursor before keystrokes.
        await asyncio.sleep(0.35)
        frontmost = await _frontmost_app_name()
        log.info("paste_into_cursor_claude: post-activate frontmost=%r", frontmost)
        if frontmost.lower() != "cursor":
            log.warning("paste_into_cursor_claude: Cursor didn't take focus (still %r)", frontmost)
            return {
                "success": False,
                "reason": "cursor_focus_lost",
                "frontmost": frontmost,
                "detail": f"Activated Cursor but {frontmost!r} grabbed focus back.",
            }

    # ── (1b) Focus the integrated terminal (the Claude pane) before paste ──
    # The right window is now front, but focus inside it might be the editor or
    # a webview. _focus_cursor_terminal confirms (via the focused element's
    # AXDescription) that a terminal pane actually holds focus before we paste.
    focus_status = await _focus_cursor_terminal()
    if focus_status != "terminal":
        # Couldn't confirm a terminal holds focus (even after Ctrl+`). Refuse to
        # paste rather than risk dumping the prompt into a source file or a
        # webview; the caller falls back to file/clipboard ship.
        log.warning(
            "paste_into_cursor_claude: aborting — terminal not focused (status=%s)",
            focus_status,
        )
        return {
            "success": False,
            "reason": "terminal_focus_failed",
            "detail": "Couldn't confirm the Claude terminal pane held focus.",
        }

    # ── (1c) Informational: log the Cursor terminal pane mix for diagnostics.
    # We trust the focus check above rather than GATING on this. The old strict
    # rule ("abort if any shell pane exists") blocked legitimate ships whenever
    # the user simply had a second shell tab open alongside Claude.
    try:
        panes = await _cursor_terminal_pane_summary()
        if panes.get("claude", 0) == 0:
            log.warning(
                "paste_into_cursor_claude: focus confirms a terminal, but no live "
                "Claude pane detected (panes=%s) — pasting anyway per focus check",
                panes.get("ttys"),
            )
    except Exception as e:
        log.debug("paste_into_cursor_claude: pane summary failed: %s", e)
    terminal_focused = True

    # ── (2) Save existing clipboard so we can restore it after paste ────
    saved_clipboard = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pbpaste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        saved_clipboard = stdout.decode()
    except Exception as e:
        log.debug(f"paste_into_cursor_claude: pbpaste save failed: {e}")
        # Non-fatal — we'll just not restore. The paste still proceeds.

    # ── (3) Put prompt on clipboard ─────────────────────────────────────
    try:
        proc = await asyncio.create_subprocess_exec(
            "pbcopy",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(input=prompt.encode()), timeout=2.0)
        if proc.returncode != 0:
            return {"success": False, "reason": "clipboard_write_failed",
                    "detail": "pbcopy returned non-zero"}
    except Exception as e:
        return {"success": False, "reason": "clipboard_write_failed",
                "detail": str(e)[:200]}

    # ── (4) Cmd+V then Return via System Events ─────────────────────────
    # Brief activate first to ensure Cursor really is the keyboard target
    # (in case the pre-flight check was racy). Then paste, brief pause,
    # then Return. Pauses are conservative — total ~250ms — chosen for
    # reliability over speed; tune down if it feels sluggish.
    # Scale the gap between Cmd+V and Return to the paste size. A big prompt
    # streams into the terminal's bracketed-paste buffer over time; if Return
    # fires before the paste finishes, the prompt lands partially or not at all
    # (osascript still exits 0, so we'd report a false success). ~0.1s baseline
    # plus 1s per 10KB, capped at 3s so a runaway prompt can't hang the flow.
    post_paste_delay = min(3.0, 0.1 + len(prompt) / 10_000)
    paste_script = (
        'tell application "Cursor" to activate\n'
        'delay 0.1\n'
        'tell application "System Events"\n'
        '    keystroke "v" using {command down}\n'
        f'    delay {post_paste_delay:.2f}\n'
        '    key code 36\n'  # 36 = Return
        'end tell'
    )
    paste_ok = False
    paste_err = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", paste_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        paste_ok = proc.returncode == 0
        if not paste_ok:
            paste_err = stderr.decode()[:200]
    except asyncio.TimeoutError:
        paste_err = "osascript timed out after 5s"
    except Exception as e:
        paste_err = str(e)[:200]

    # ── (5) Restore the user's prior clipboard ──────────────────────────
    # Wait a moment so the paste has consumed the prompt before we
    # overwrite. If we restore too quickly the paste might pick up the
    # restored content instead.
    await asyncio.sleep(0.3)
    if saved_clipboard:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbcopy",
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(
                proc.communicate(input=saved_clipboard.encode()),
                timeout=2.0,
            )
        except Exception as e:
            log.debug(f"paste_into_cursor_claude: clipboard restore failed: {e}")

    if not paste_ok:
        log.warning(f"paste_into_cursor_claude osascript failed: {paste_err}")
        return {"success": False, "reason": "applescript_failed", "detail": paste_err}

    # ── (6) Optional stealth: return focus to whatever was frontmost before.
    # OFF by default — restoring focus to Chrome right after pasting made the
    # ship look like it did nothing (Chrome popped back up, no prompt visible
    # in the terminal). Leaving Cursor front lets the user watch the prompt
    # land. Callers that genuinely want to stay put pass restore_focus=True.
    if restore_focus and original_frontmost and original_frontmost.lower() != "cursor":
        restore_script = f'tell application "{original_frontmost}" to activate'
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", restore_script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, restore_err = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            if proc.returncode != 0:
                log.debug(
                    "paste_into_cursor_claude: focus restore to %r failed: %s",
                    original_frontmost, restore_err.decode()[:120],
                )
        except Exception as e:
            log.debug(f"paste_into_cursor_claude: focus restore exception: {e}")

    log.info(
        f"paste_into_cursor_claude succeeded (prompt_len={len(prompt)}, "
        f"terminal_focused={terminal_focused}, "
        f"restored_focus_to={original_frontmost if restore_focus else None!r})"
    )
    return {"success": True, "verified": terminal_focused}


async def monitor_build(project_dir: str, ws=None, synthesize_fn=None) -> None:
    """Monitor a Claude Code build for completion. Notify via WebSocket when done."""
    import base64

    output_file = Path(project_dir) / ".valet_output.txt"
    start = time.time()
    timeout = 600  # 10 minutes

    while time.time() - start < timeout:
        await asyncio.sleep(5)
        if output_file.exists():
            content = output_file.read_text()
            if "--- VALET TASK COMPLETE ---" in content:
                log.info(f"Build complete in {project_dir}")
                if ws and synthesize_fn:
                    try:
                        msg = "The build is complete, sir."
                        audio_bytes = await synthesize_fn(msg)
                        if audio_bytes:
                            encoded = base64.b64encode(audio_bytes).decode()
                            await ws.send_json({"type": "status", "state": "speaking"})
                            await ws.send_json({"type": "audio", "data": encoded, "text": msg})
                            await ws.send_json({"type": "status", "state": "idle"})
                    except Exception as e:
                        log.warning(f"Build notification failed: {e}")
                return

    log.warning(f"Build timed out in {project_dir}")


async def execute_action(intent: dict, projects: list = None) -> dict:
    """Route a classified intent to the right action function.

    Args:
        intent: {"action": str, "target": str} from classify_intent()
        projects: list of known project dicts for resolving working dirs

    Returns: {"success": bool, "confirmation": str, "project_dir": str | None}
    """
    action = intent.get("action", "chat")
    target = intent.get("target", "")

    if action == "open_terminal":
        result = await open_terminal("claude --dangerously-skip-permissions")
        result["project_dir"] = None
        return result

    elif action == "browse":
        if target.startswith("http://") or target.startswith("https://"):
            url = target
        else:
            url = f"https://www.google.com/search?q={quote(target)}"

        # Detect which browser user wants
        target_lower = target.lower()
        if "firefox" in target_lower:
            browser = "firefox"
        else:
            browser = "chrome"

        result = await open_browser(url, browser)
        result["project_dir"] = None
        return result

    elif action == "build":
        # Create project folder on Desktop, spawn Claude Code
        project_name = _generate_project_name(target)
        project_dir = str(DESKTOP_PATH / project_name)
        os.makedirs(project_dir, exist_ok=True)
        result = await open_claude_in_project(project_dir, target)
        result["project_dir"] = project_dir
        return result

    else:
        return {"success": False, "confirmation": "", "project_dir": None}


def _generate_project_name(prompt: str) -> str:
    """Generate a kebab-case project folder name from the prompt."""
    # First: check for a quoted name like "tiktok-analytics-dashboard"
    quoted = re.search(r'"([^"]+)"', prompt)
    if quoted:
        name = quoted.group(1).strip()
        # Already kebab-case or close to it
        name = re.sub(r"[^a-zA-Z0-9\s-]", "", name).strip()
        if name:
            return re.sub(r"[\s]+", "-", name.lower())

    # Second: check for "called X" or "named X" pattern
    called = re.search(r'(?:called|named)\s+(\S+(?:[-_]\S+)*)', prompt, re.IGNORECASE)
    if called:
        name = re.sub(r"[^a-zA-Z0-9-]", "", called.group(1))
        if len(name) > 3:
            return name.lower()

    # Fallback: extract meaningful words
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt.lower()).split()
    skip = {"a", "the", "an", "me", "build", "create", "make", "for", "with", "and",
            "to", "of", "i", "want", "need", "new", "project", "directory", "called",
            "on", "desktop", "that", "application", "app", "full", "stack", "simple",
            "web", "page", "site", "named"}
    meaningful = [w for w in words if w not in skip and len(w) > 2][:4]
    return "-".join(meaningful) if meaningful else "valet-project"
