"""
VALET Apple Notes Access — READ + CREATE ONLY.

Can read existing notes and create new ones.
CANNOT edit or delete existing notes (safety).
"""

import asyncio
import logging
import re

log = logging.getLogger("valet.notes")


def _norm(s: str) -> str:
    """Collapse a title to comparable form: lowercase, alphanumerics only. This
    bridges speech-to-text seams the literal AppleScript `contains` misses —
    e.g. "new employee on boarding" and "New Employee Onboarding" both become
    "newemployeeonboarding"."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def _run_notes_script(script: str, timeout: float = 10) -> str:
    """Run an AppleScript against Notes.app."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            log.warning(f"Notes script failed: {stderr.decode()[:200]}")
            return ""
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        log.warning("Notes script timed out")
        return ""
    except Exception as e:
        log.warning(f"Notes script error: {e}")
        return ""


async def get_recent_notes(count: int = 10) -> list[dict]:
    """Get most recent notes (title + creation date)."""
    script = f'''
tell application "Notes"
    set output to ""
    set allNotes to every note
    set limit to count of allNotes
    if limit > {count} then set limit to {count}
    repeat with i from 1 to limit
        set n to item i of allNotes
        set nName to name of n
        set nDate to creation date of n as string
        try
            set nFolder to name of container of n
        on error
            set nFolder to "Notes"
        end try
        set output to output & nName & "|||" & nDate & "|||" & nFolder & linefeed
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script, timeout=15)
    if not raw:
        return []
    notes = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 3:
            notes.append({
                "title": parts[0].strip(),
                "date": parts[1].strip(),
                "folder": parts[2].strip(),
            })
    return notes


async def _recent_titles(count: int = 200) -> list[str]:
    """Just the note names — deliberately avoids `container of n`, which throws
    (-1728) on some notes and aborts the folder-aware get_recent_notes script.
    Used by read_note's normalized fallback, which only needs titles."""
    script = f'''
tell application "Notes"
    set output to ""
    set allNotes to every note
    set lim to count of allNotes
    if lim > {count} then set lim to {count}
    repeat with i from 1 to lim
        set output to output & (name of item i of allNotes) & linefeed
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script, timeout=15)
    return [t.strip() for t in raw.split("\n") if t.strip()]


async def _read_note_by_exact_title(title: str) -> dict | None:
    """Read one note whose name is exactly `title`. Returns title + body."""
    escaped = title.replace('"', '\\"')
    script = f'''
tell application "Notes"
    repeat with n in every note
        if name of n is "{escaped}" then
            set nBody to plaintext of n
            if length of nBody > 3000 then set nBody to text 1 thru 3000 of nBody
            return name of n & "|||" & nBody
        end if
    end repeat
    return ""
end tell
'''
    raw = await _run_notes_script(script, timeout=10)
    if not raw or "|||" not in raw:
        return None
    title_, _, body = raw.partition("|||")
    return {"title": title_.strip(), "body": body.strip()}


async def _resolve_note_title(title_match: str) -> str | None:
    """Best-matching note NAME for a query: a literal substring match first, then
    a normalized match so speech-to-text seams (e.g. "on boarding" vs
    "Onboarding") still resolve. Returns the note's exact name, or None.
    Shared by read_note (reads its body) and open_note (shows it in Notes)."""
    escaped = title_match.replace('"', '\\"')
    script = f'''
tell application "Notes"
    repeat with n in every note
        if name of n contains "{escaped}" then return name of n
    end repeat
    return ""
end tell
'''
    raw = await _run_notes_script(script, timeout=10)
    if raw.strip():
        return raw.strip()

    # Fallback: normalize both sides and match on the collapsed forms. Picks the
    # closest title (a containment match with the smallest length gap) so a short
    # query doesn't latch onto an unrelated longer note that merely contains it.
    q = _norm(title_match)
    if not q:
        return None
    best, best_gap = None, None
    for t in await _recent_titles(200):
        nt = _norm(t)
        if nt and (q in nt or nt in q):
            gap = abs(len(nt) - len(q))
            if best_gap is None or gap < best_gap:
                best, best_gap = t, gap
    if best:
        log.info(f"note title fuzzy-matched {title_match!r} -> {best!r}")
    return best


async def read_note(title_match: str) -> dict | None:
    """Read a note's title + body, or None if nothing matches."""
    title = await _resolve_note_title(title_match)
    if not title:
        return None
    return await _read_note_by_exact_title(title)


async def open_note(title_match: str) -> dict | None:
    """Open Notes.app and SHOW the best-matching note (don't read it aloud).
    Returns {"title": <exact name>} on success, or None if nothing matches."""
    title = await _resolve_note_title(title_match)
    if not title:
        return None
    escaped = title.replace('"', '\\"')
    script = f'''
tell application "Notes"
    activate
    set matches to (every note whose name is "{escaped}")
    if (count of matches) > 0 then
        show item 1 of matches
        return name of item 1 of matches
    end if
    return ""
end tell
'''
    raw = await _run_notes_script(script, timeout=10)
    return {"title": title} if raw.strip() else None


async def search_notes_apple(query: str, count: int = 5) -> list[dict]:
    """Search notes by title keyword."""
    escaped = query.replace('"', '\\"')
    script = f'''
tell application "Notes"
    set output to ""
    set foundCount to 0
    set allNotes to every note
    repeat with n in allNotes
        if foundCount >= {count} then exit repeat
        if name of n contains "{escaped}" then
            set output to output & name of n & "|||" & (creation date of n as string) & linefeed
            set foundCount to foundCount + 1
        end if
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script, timeout=15)
    if not raw:
        return []
    notes = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 2:
            notes.append({"title": parts[0].strip(), "date": parts[1].strip()})
    return notes


async def create_apple_note(title: str, body: str, folder: str = "Notes") -> bool:
    """Create a new note in Apple Notes with HTML support for formatting.

    Supports checklist items: lines starting with "- [ ]" or "- [x]" become checkboxes.
    """
    # Convert markdown-style checklists to HTML
    html_body = _body_to_html(body)

    escaped_title = title.replace('"', '\\"')
    escaped_body = html_body.replace('"', '\\"')
    escaped_folder = folder.replace('"', '\\"')
    script = f'''
tell application "Notes"
    tell folder "{escaped_folder}"
        make new note with properties {{name:"{escaped_title}", body:"{escaped_body}"}}
    end tell
    return "OK"
end tell
'''
    result = await _run_notes_script(script, timeout=10)
    if result == "OK":
        log.info(f"Created Apple Note: {title}")
        return True
    return False


def _as_str(s: str) -> str:
    """Escape a Python string for embedding in an AppleScript double-quoted
    literal (backslash and double-quote). Newlines are left literal — valid
    inside an AppleScript string."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def _build_checklist_script(title: str, plain_html: str, glyph_html: str,
                            tasks: list[str], folder: str) -> str:
    """AppleScript that creates the note with its full text ALREADY in place
    (`plain_html`, written silently — no keystrokes), then converts each task line
    to a native tickable checklist by driving the Notes UI: Find the task text,
    then click Format ▸ Checklist (NOT the ⇧⌘L shortcut, which users may have
    rebound — e.g. to Loom). It re-asserts Notes as frontmost before every step and
    BAILS to the ☐-glyph body if focus is lost, so keystrokes can never leak into
    another app. Returns 'ok', 'fallback', or an error string."""
    tasks_literal = "{" + ", ".join('"' + _as_str(t) + '"' for t in tasks) + "}"
    return f'''
set plainBody to "{_as_str(plain_html)}"
set glyphBody to "{_as_str(glyph_html)}"
set taskList to {tasks_literal}
tell application "Notes"
	activate
	set theNote to make new note at folder "{_as_str(folder)}" with properties {{name:"{_as_str(title)}", body:plainBody}}
	show theNote
end tell
delay 1.2
tell application "System Events"
	tell process "Notes" to set frontmost to true
end tell
delay 0.4
tell application "System Events"
	if (name of first application process whose frontmost is true) is not "Notes" then
		tell application "Notes" to set body of theNote to glyphBody
		return "fallback"
	end if
end tell
repeat with t in taskList
	tell application "System Events"
		tell process "Notes" to set frontmost to true
		delay 0.35
		if (name of first application process whose frontmost is true) is not "Notes" then
			tell application "Notes" to set body of theNote to glyphBody
			return "fallback"
		end if
		keystroke "f" using {{command down}}
		delay 0.35
		keystroke (t as text)
		delay 0.45
		key code 36
		delay 0.35
		key code 53
		delay 0.3
		tell process "Notes" to click menu item "Checklist" of menu "Format" of menu bar item "Format" of menu bar 1
		delay 0.35
	end tell
end repeat
return "ok"
'''


async def create_apple_note_with_checklists(title: str, body: str,
                                            folder: str = "Notes") -> bool:
    """Create a note whose "- [ ]" task lines become NATIVE, tap-to-check Notes
    checklist items.

    Apple Notes' scripting `body` is checklist-blind (it can't create OR read a
    checkbox), so the only way to a real checklist is the Notes UI. This writes the
    full note text silently via `body` FIRST (so nothing is ever typed and no text
    can leak), then drives the UI to convert just the task lines — Find each task,
    then Format ▸ Checklist. If Notes isn't frontmost at any step it abandons the UI
    and leaves the ☐-glyph body instead, so a save always yields a complete note.

    Falls back to the plain `create_apple_note` path when there are no task lines
    (e.g. a screen summary), which needs no UI automation."""
    tasks: list[str] = []
    plain_lines: list[str] = []
    for line in body.split("\n"):
        m = re.match(r"^-\s*\[\s?\]\s*(.+)", line.strip())
        if m:
            txt = m.group(1).strip()
            tasks.append(txt)
            plain_lines.append(txt)          # plain text — the Find target
        else:
            plain_lines.append(line)
    if not tasks:
        return await create_apple_note(title, body, folder)

    plain_html = _body_to_html("\n".join(plain_lines))   # tasks as plain text
    glyph_html = _body_to_html(body)                     # tasks as ☐ (fallback)
    script = _build_checklist_script(title, plain_html, glyph_html, tasks, folder)
    # UI automation with per-task delays — scale the timeout with the task count.
    result = await _run_notes_script(script, timeout=20 + 4 * len(tasks))
    log.info(f"Created checklist note '{title}': {result or '(no result)'}")
    # 'ok'/'fallback' both created a complete note; "" means osascript failed or
    # timed out (a partial note may exist, but report failure so the caller says so).
    return result in ("ok", "fallback")


def _body_to_html(body: str) -> str:
    """Convert plain text / markdown to HTML for Apple Notes.

    Supports:
    - Checklist items: "- [ ] task" / "- [x] task" → a ☐ / ☑ glyph line
    - Bullet points: "- item" → bullet
    - Numbered lists: "1. item" → numbered
    - Plain text → paragraphs

    NOTE on checkboxes: `<input type="checkbox">` is NOT used — modern Apple Notes
    (macOS 26 / Notes 4.13, verified) silently DROPS <input> on AppleScript body
    import, leaving the task as bare text indistinguishable from other lines. No
    HTML markup (Apple-dash-list, todo lists, etc.) produces a native *tickable*
    checklist via AppleScript on this version. The ☐/☑ glyph is the reliable way to
    render a checkbox that's visible and distinct; it isn't tap-to-check, but a
    real tickable checklist would need GUI automation, not body HTML."""
    import re
    lines = body.split("\n")
    html_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append("<br>")
        elif re.match(r"^-\s*\[x\]\s*", stripped, re.IGNORECASE):
            text = re.sub(r"^-\s*\[x\]\s*", "", stripped, flags=re.IGNORECASE)
            html_lines.append(f"<div>&#9745; {text}</div>")   # ☑ checked
        elif re.match(r"^-\s*\[\s?\]\s*", stripped):
            text = re.sub(r"^-\s*\[\s?\]\s*", "", stripped)
            html_lines.append(f"<div>&#9744; {text}</div>")   # ☐ unchecked
        elif re.match(r"^[-*+]\s+", stripped):
            text = re.sub(r"^[-*+]\s+", "", stripped)
            html_lines.append(f"<div>• {text}</div>")
        elif re.match(r"^\d+\.\s+", stripped):
            text = re.sub(r"^\d+\.\s+", "", stripped)
            html_lines.append(f"<div>{stripped}</div>")
        elif stripped.startswith("#"):
            text = re.sub(r"^#+\s*", "", stripped)
            html_lines.append(f"<h2>{text}</h2>")
        else:
            html_lines.append(f"<div>{stripped}</div>")

    return "\n".join(html_lines)


async def get_note_folders() -> list[str]:
    """Get list of note folder names."""
    script = '''
tell application "Notes"
    set output to ""
    repeat with f in every folder
        set output to output & name of f & linefeed
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script)
    return [f.strip() for f in raw.split("\n") if f.strip()]
