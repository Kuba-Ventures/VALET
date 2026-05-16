"""
JARVIS Action Executor — AppleScript-based system actions.

Execute actions IMMEDIATELY, before generating any LLM response.
Each function returns {"success": bool, "confirmation": str}.
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("jarvis.actions")

DESKTOP_PATH = Path.home() / "Desktop"


async def _mark_terminal_as_jarvis(revert_after: float = 5.0):
    """Temporarily set the front Terminal window to Ocean theme, then revert.

    Shows the user JARVIS is active in that terminal. Reverts after revert_after seconds.
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
    """Open Terminal.app and optionally run a command. Marks it blue for JARVIS."""
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
        await _mark_terminal_as_jarvis()
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


async def type_into_app(target: str, press_enter: bool = False) -> dict:
    """Activate an app (if specified) and type text into it via System Events.

    Target format: "AppName ||| text to type"  or  "text to type" (uses current app).
    If press_enter is True, presses Return after typing (sends the message / runs the command).
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
        return {"success": False, "confirmation": "I didn't catch what to type, sir."}

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
                "confirmation": "I need Accessibility permission to type, sir. Open System Settings → Privacy & Security → Accessibility and enable the JARVIS process.",
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
    return {"success": True, "confirmation": f"{verb} in {where}, sir."}


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


async def new_cursor_project(name: str, base_dir: str | None = None, task_id: str | None = None) -> dict:
    """Create a fresh project directory and open it in Cursor as a new session.

    name: project folder name (kebab-cased internally if necessary)
    base_dir: optional parent (default: ~/Code, falling back to ~/Desktop)
    task_id: optional process-panel task_id; events emitted in a follow-up commit.
    """
    _ = task_id  # accepted for forward compatibility
    raw_name = name.strip()
    if not raw_name:
        return {"success": False, "confirmation": "I need a project name, sir."}

    # Sanitize name to a safe filesystem slug.
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-")
    if not slug:
        return {"success": False, "confirmation": "That name doesn't translate to a folder, sir."}

    home = Path.home()
    if base_dir:
        base = Path(base_dir).expanduser()
    else:
        # Prefer ~/Code if it exists, otherwise ~/Desktop
        base = home / "Code" if (home / "Code").exists() else home / "Desktop"

    base.mkdir(parents=True, exist_ok=True)
    project_path = base / slug

    if project_path.exists():
        # If the dir already exists, just open it (don't error — user may be resuming).
        log.info(f"new_cursor_project: '{project_path}' already exists, opening as-is")
    else:
        project_path.mkdir(parents=True)
        # Drop a minimal README so the folder isn't empty (Cursor file tree feels alive).
        (project_path / "README.md").write_text(f"# {raw_name}\n")

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
            return {
                "success": False,
                "confirmation": f"I created {project_path} but couldn't open it in Cursor, sir.",
            }

    # After Cursor finishes loading the workspace, open the integrated terminal
    # and split it so the user sees two terminal panes side by side.
    # Key codes: 50 = backtick (`), 42 = backslash (\). Defaults in VS Code /
    # Cursor: Ctrl+` toggles terminal, Cmd+\ splits the active terminal.
    asyncio.create_task(_open_split_terminal_in_cursor())

    return {
        "success": True,
        "confirmation": f"New Cursor session up at {project_path}, sir.",
        "path": str(project_path),
    }


async def _open_split_terminal_in_cursor() -> None:
    """Wait for Cursor to be ready, then open terminal + split it.

    Runs as a background task so the calling action returns quickly. Cursor
    needs ~1.5s after launch before keystrokes register reliably.
    """
    await asyncio.sleep(1.8)
    script = (
        'tell application "Cursor" to activate\n'
        'delay 0.4\n'
        'tell application "System Events"\n'
        '    key code 50 using control down\n'  # Ctrl+`  → toggle terminal
        '    delay 0.5\n'
        '    key code 42 using command down\n'  # Cmd+\   → split terminal
        'end tell'
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(f"split-terminal keystroke failed: {stderr.decode()}")


async def refresh_calendar_tabs() -> dict:
    """Reload any open Google Calendar tab in Chrome so the user sees fresh data.

    Used after JARVIS creates/cancels/updates events via the API. The user
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


async def open_app_or_path(target: str) -> dict:
    """Open a macOS app by name or a filesystem path in Finder.

    Routes:
      - "Finder" / "Slack" / "Notes" / "Spotify" → `open -a <App Name>`
      - "/Users/foo/Desktop" / "~/Documents" → expanded then `open <path>`
      - "Desktop" (bare folder name) → opens that subfolder of $HOME if it exists
    """
    raw = target.strip()
    if not raw:
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
    return {
        "success": success,
        "confirmation": f"{raw} is up, sir." if success else f"I couldn't find an app called {raw}, sir.",
    }


async def open_browser(url: str, browser: str = "chrome") -> dict:
    """Open URL in user's browser (Chrome or Firefox).

    Uses macOS `open -a` rather than AppleScript so the URL is routed to the
    main browser app bundle via Launch Services. AppleScript's
    `tell application "Google Chrome"` can misfire when a Chrome PWA window
    (e.g. the JARVIS app shell) is in the foreground — it targets the PWA's
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

    Writes the prompt to CLAUDE.md (which claude reads automatically on startup)
    then launches claude in interactive mode with --dangerously-skip-permissions.
    No prompt escaping needed — CLAUDE.md handles context delivery.
    """
    # Write prompt to CLAUDE.md — claude reads this automatically
    claude_md = Path(project_dir) / "CLAUDE.md"
    claude_md.write_text(f"# Task\n\n{prompt}\n\nBuild this completely. If web app, make index.html work standalone.\n")

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
        await _mark_terminal_as_jarvis()
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
            await _mark_terminal_as_jarvis()

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


async def monitor_build(project_dir: str, ws=None, synthesize_fn=None) -> None:
    """Monitor a Claude Code build for completion. Notify via WebSocket when done."""
    import base64

    output_file = Path(project_dir) / ".jarvis_output.txt"
    start = time.time()
    timeout = 600  # 10 minutes

    while time.time() - start < timeout:
        await asyncio.sleep(5)
        if output_file.exists():
            content = output_file.read_text()
            if "--- JARVIS TASK COMPLETE ---" in content:
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
    return "-".join(meaningful) if meaningful else "jarvis-project"
