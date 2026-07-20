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
    # activate + `show` front Notes and open the new note (see _create_note_html).
    script = f'''
tell application "Notes"
    activate
    tell folder "{escaped_folder}"
        set newNote to make new note with properties {{name:"{escaped_title}", body:"{escaped_body}"}}
    end tell
    show newNote
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


def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title_header_html(title: str) -> str:
    """The standard summary-note header (issue #310, "Big title" option): the title
    as an <h1> so Apple Notes renders it in its big, bold Title style. We do NOT set
    the note's `name` separately — Notes derives the title/name from this first line,
    which avoids a duplicate title line. Applies to every summary note (Gmail,
    screen, and future doc/web/task summaries)."""
    return f"<h1>{_html_escape(title)}</h1>\n"


async def _create_note_html(body_html: str, folder: str = "Notes") -> bool:
    """Create a note straight from ready HTML, WITHOUT a `name` (so the leading
    <h1> becomes the title). Used for the no-checklist summary path."""
    # activate + `show` bring Notes to the front and open the just-created note,
    # so a summary→note (e.g. "put that in a note") visibly lands in a fronted
    # window rather than being written silently in the background.
    script = f'''
tell application "Notes"
    activate
    tell folder "{_as_str(folder)}"
        set newNote to make new note with properties {{body:"{_as_str(body_html)}"}}
    end tell
    show newNote
    return "OK"
end tell
'''
    return (await _run_notes_script(script, timeout=10)) == "OK"


def _task_lines_from_body(body: str) -> list[str]:
    """Pull the action-item text out of a note body's "- [ ]" lines (order kept)."""
    out = []
    for line in body.split("\n"):
        m = re.match(r"^-\s*\[\s?\]\s*(.+)", line.strip())
        if m:
            out.append(m.group(1).strip())
    return out


_REMINDERS_LIST = "VALET"


async def add_reminders(tasks: list[str], list_name: str = _REMINDERS_LIST) -> int:
    """Create each task as a native Apple Reminders item — real, tap-to-check
    reminders — SILENTLY via AppleScript. Unlike a Notes checklist this needs no UI
    automation, so it never takes over the screen or the keyboard. Groups them in a
    dedicated list (created on first use) so digest tasks stay together and don't
    clutter the user's default list. Returns the number created."""
    tasks = [t.strip() for t in (tasks or []) if t and t.strip()]
    if not tasks:
        return 0
    makes = "\n        ".join(
        f'make new reminder with properties {{name:"{_as_str(t)}"}}' for t in tasks)
    script = f'''
tell application "Reminders"
    if not (exists list "{_as_str(list_name)}") then make new list with properties {{name:"{_as_str(list_name)}"}}
    tell list "{_as_str(list_name)}"
        {makes}
    end tell
    return "OK"
end tell
'''
    # Generous timeout: the FIRST call can be slow — Reminders may cold-start and
    # macOS may show a one-time "control Reminders" automation prompt.
    ok = (await _run_notes_script(script, timeout=30)) == "OK"
    if ok:
        log.info(f"Added {len(tasks)} reminder(s) to '{list_name}'")
    return len(tasks) if ok else 0


async def save_summary_note_and_reminders(title: str, body: str,
                                          folder: str = "Notes") -> tuple[bool, int]:
    """Save a summary two ways, both SILENTLY (no screen/keyboard takeover):
      1) a Notes note — the readable summary, with the big <h1> title header and
         each action item shown as a ☐ glyph line (visual, distinct from prose);
      2) each action item ALSO as a native, tap-to-check Apple Reminder.

    This is the resolution to the Notes-checklist problem: Notes can't create a
    tickable checkbox without driving its UI (which locks the machine for the whole
    save), so the *tappable* copy of each task lives in Reminders — created via the
    scripting API with zero UI. Returns (note_created, reminders_added)."""
    note_ok = await _create_note_html(_title_header_html(title) + _body_to_html(body),
                                      folder)
    n = await add_reminders(_task_lines_from_body(body))
    return note_ok, n


def _body_to_html(body: str) -> str:
    """Convert plain text / markdown to HTML for Apple Notes.

    Supports:
    - Checklist items: "- [ ] task" / "- [x] task" → a ☐ / ☑ glyph line
    - Bold line: "**heading**" → a bold paragraph (Notes honors <b> on import)
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
        elif stripped.startswith("**") and stripped.endswith("**") and len(stripped) >= 5:
            text = stripped[2:-2].strip()   # **heading** → bold line
            html_lines.append(f"<div><b>{text}</b></div>")
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
