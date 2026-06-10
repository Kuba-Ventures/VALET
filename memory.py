"""
VALET Memory & Planning — persistent context, tasks, notes, and smart routing.

Three systems:
1. Memory — facts, preferences, project context VALET learns from conversations
2. Tasks — to-do items with priority, due dates, project association
3. Notes — freeform context tied to projects, people, or topics

Everything stored in SQLite. Relevant memories injected into every LLM call
so VALET gets smarter over time.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("valet.memory")

DB_PATH = Path(__file__).parent / "data" / "valet.db"

# Bump when adding migrations below. PRAGMA user_version is checked on init.
SCHEMA_VERSION = 3


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending schema migrations based on PRAGMA user_version.

    Each migration block runs only when crossing its version boundary. After
    all applicable blocks run, user_version is bumped to SCHEMA_VERSION.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return

    if current < 1:
        # v1: project_aliases for the design-partner project lifecycle.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS project_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                last_opened_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_project_aliases_path ON project_aliases(path);
        """)

    if current < 2:
        # v2: design_sessions for Phase 3 design-partner audit trail.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS design_sessions (
                id TEXT PRIMARY KEY,
                topic TEXT,
                project_path TEXT,
                started_at REAL,
                finished_at REAL,
                final_prompt TEXT DEFAULT '',
                status TEXT DEFAULT 'designing',
                self_mod INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_design_sessions_status ON design_sessions(status);
        """)

    if current < 3:
        # v3: Phase 4 ship-it audit columns — how it shipped + where it landed.
        # ALTER ADD COLUMN is idempotent-by-name only; guard with try/except to
        # tolerate re-runs in dev environments that may have the column already.
        for stmt in (
            "ALTER TABLE design_sessions ADD COLUMN ship_method TEXT DEFAULT ''",
            "ALTER TABLE design_sessions ADD COLUMN inbox_path TEXT DEFAULT ''",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    log.info(f"Schema migrated to version {SCHEMA_VERSION}")


def init_db():
    """Create tables if they don't exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,          -- 'fact', 'preference', 'project', 'person', 'decision'
            content TEXT NOT NULL,
            source TEXT DEFAULT '',      -- what conversation/context it came from
            importance INTEGER DEFAULT 5, -- 1-10, higher = more important
            created_at REAL NOT NULL,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority TEXT DEFAULT 'medium', -- 'high', 'medium', 'low'
            status TEXT DEFAULT 'open',     -- 'open', 'in_progress', 'done', 'cancelled'
            due_date TEXT,                  -- ISO date string
            due_time TEXT,                  -- HH:MM
            project TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',         -- JSON array
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL,
            completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '',
            content TEXT NOT NULL,
            topic TEXT DEFAULT '',       -- project name, person, or topic
            tags TEXT DEFAULT '[]',      -- JSON array
            created_at REAL NOT NULL,
            updated_at REAL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            content, type, source,
            content='memories', content_rowid='id'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS task_fts USING fts5(
            title, description, project, notes,
            content='tasks', content_rowid='id'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
            title, content, topic,
            content='notes', content_rowid='id'
        );
    """)
    _run_migrations(conn)
    conn.close()
    log.info("Memory database initialized")


# ---------------------------------------------------------------------------
# Project aliases — fuzzy "open <name>" resolution for design-partner mode
# ---------------------------------------------------------------------------

def _normalize_project_key(name: str) -> str:
    """Canonical project-name key: split camelcase, lowercase, collapse separators.

    Examples:
      RecipeBook Code        -> recipe-book-code
      TommyTopDecker-Trading -> tommy-top-decker-trading
      valet-main            -> valet-main
      tommytopdecker         -> tommytopdecker  (no camelcase boundary)
    """
    import re as _re
    # Camelcase split: insert '-' between lowercase->uppercase transitions.
    name = _re.sub(r"([a-z])([A-Z])", r"\1-\2", name)
    return _re.sub(r"[\s_.\-]+", "-", name.lower()).strip("-")


def _keys_match(input_key: str, folder_key: str, exact: bool = False) -> bool:
    """Match two normalized keys with hyphen-tolerant fallback.

    The camelcase fix can leave inputs and folders disagreeing about hyphen
    placement (e.g., spoken "tommytopdecker" vs folder "tommy-top-decker-trading").
    So matching also tries the compressed (hyphenless) form of each side.
    """
    if exact:
        return (input_key == folder_key) or (
            input_key.replace("-", "") == folder_key.replace("-", "")
        )
    return (input_key in folder_key) or (
        input_key.replace("-", "") in folder_key.replace("-", "")
    )


def resolve_project(name: str) -> str | None:
    """Resolve a project name to a filesystem path via the alias table.

    Matches by canonical key (case-insensitive, separator-insensitive,
    camelcase-tolerant, hyphen-fallback) so "valet main", "valet_main",
    "Valet.Main", "valet-main" all resolve to the same row.
    Returns None if nothing matches.
    """
    if not name or not name.strip():
        return None
    key = _normalize_project_key(name)
    conn = _get_db()
    rows = conn.execute("SELECT alias, path FROM project_aliases").fetchall()
    conn.close()
    for row in rows:
        if _keys_match(key, _normalize_project_key(row["alias"]), exact=True):
            return row["path"]
    return None


def delete_alias(alias: str) -> bool:
    """Delete an alias row by name (case + separator insensitive). Returns True if anything was deleted."""
    if not alias:
        return False
    key = _normalize_project_key(alias)
    conn = _get_db()
    rows = conn.execute("SELECT id, alias FROM project_aliases").fetchall()
    ids_to_delete = [
        r["id"] for r in rows
        if _keys_match(key, _normalize_project_key(r["alias"]), exact=True)
    ]
    if ids_to_delete:
        conn.executemany(
            "DELETE FROM project_aliases WHERE id = ?",
            [(i,) for i in ids_to_delete],
        )
        conn.commit()
    conn.close()
    return bool(ids_to_delete)


def update_alias_path(alias: str, new_path: str) -> bool:
    """Update an alias's path. Returns True if anything was updated."""
    if not alias:
        return False
    key = _normalize_project_key(alias)
    conn = _get_db()
    rows = conn.execute("SELECT id, alias FROM project_aliases").fetchall()
    ids = [
        r["id"] for r in rows
        if _keys_match(key, _normalize_project_key(r["alias"]), exact=True)
    ]
    if ids:
        conn.executemany(
            "UPDATE project_aliases SET path = ? WHERE id = ?",
            [(new_path, i) for i in ids],
        )
        conn.commit()
    conn.close()
    return bool(ids)


def cleanup_stale_aliases(known_roots: list) -> dict:
    """One-shot scan of project_aliases. Repair moved projects, delete truly orphaned rows.

    For each row whose stored path doesn't exist:
      - If exactly one configured root contains a folder with the same basename,
        UPDATE the row to point there (silent self-heal).
      - Otherwise DELETE the row (it's cruft; user can re-register).

    Returns {"repaired": [...], "deleted": [...]} for logging.
    """
    from pathlib import Path
    conn = _get_db()
    rows = conn.execute("SELECT id, alias, path FROM project_aliases").fetchall()
    repaired: list[dict] = []
    deleted: list[dict] = []
    for row in rows:
        stored_path = Path(row["path"])
        if stored_path.exists():
            continue
        basename = stored_path.name
        candidates: list[Path] = []
        for root in known_roots:
            root_path = Path(root)
            if not root_path.exists():
                continue
            candidate = root_path / basename
            if candidate.exists():
                candidates.append(candidate)
        if len(candidates) == 1:
            new_path = str(candidates[0])
            conn.execute(
                "UPDATE project_aliases SET path = ? WHERE id = ?",
                (new_path, row["id"]),
            )
            repaired.append({"alias": row["alias"], "old": row["path"], "new": new_path})
        else:
            conn.execute("DELETE FROM project_aliases WHERE id = ?", (row["id"],))
            deleted.append({
                "alias": row["alias"],
                "path": row["path"],
                "reason": "ambiguous" if candidates else "orphaned",
            })
    conn.commit()
    conn.close()
    if repaired or deleted:
        log.info(f"cleanup_stale_aliases: repaired={len(repaired)}, deleted={len(deleted)}")
    return {"repaired": repaired, "deleted": deleted}


def record_project(alias: str, path: str) -> None:
    """Insert or update an alias → path mapping, stamping last_opened_at."""
    if not alias or not path:
        return
    conn = _get_db()
    conn.execute(
        "INSERT INTO project_aliases (alias, path, last_opened_at) VALUES (?, ?, ?) "
        "ON CONFLICT(alias) DO UPDATE SET path = excluded.path, last_opened_at = excluded.last_opened_at",
        (alias.strip(), path, time.time())
    )
    conn.commit()
    conn.close()


def touch_project(path: str) -> None:
    """Bump last_opened_at for whichever alias(es) point at this path."""
    if not path:
        return
    conn = _get_db()
    conn.execute(
        "UPDATE project_aliases SET last_opened_at = ? WHERE path = ?",
        (time.time(), path)
    )
    conn.commit()
    conn.close()


def list_known_projects() -> list[dict]:
    """All recorded project aliases, most-recently-opened first."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT alias, path, last_opened_at FROM project_aliases "
        "ORDER BY (last_opened_at IS NULL), last_opened_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Memories — facts VALET learns
# ---------------------------------------------------------------------------

def remember(content: str, mem_type: str = "fact", source: str = "", importance: int = 5) -> int:
    """Store a memory. Returns the memory ID."""
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO memories (type, content, source, importance, created_at) VALUES (?, ?, ?, ?, ?)",
        (mem_type, content, source, importance, time.time())
    )
    mem_id = cur.lastrowid
    # Update FTS
    conn.execute(
        "INSERT INTO memory_fts (rowid, content, type, source) VALUES (?, ?, ?, ?)",
        (mem_id, content, mem_type, source)
    )
    conn.commit()
    conn.close()
    log.info(f"Stored memory [{mem_type}]: {content[:60]}")
    return mem_id


def _sanitize_fts_query(query: str) -> str:
    """Clean a query string for FTS5 — remove special characters that break it."""
    # Remove apostrophes, quotes, and FTS operators
    cleaned = query.replace("'", "").replace('"', "").replace("*", "").replace("-", " ")
    # Take meaningful words only
    words = [w for w in cleaned.split() if len(w) > 2]
    if not words:
        return ""
    # Join with OR for broader matching
    return " OR ".join(words[:5])


def recall(query: str, limit: int = 5) -> list[dict]:
    """Search memories by relevance. Returns most relevant matches."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = _get_db()
    try:
        results = conn.execute("""
            SELECT m.id, m.type, m.content, m.importance, m.created_at, m.access_count
            FROM memory_fts f
            JOIN memories m ON f.rowid = m.id
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        results = []

    # Update access counts
    for r in results:
        conn.execute(
            "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (time.time(), r["id"])
        )
    conn.commit()
    conn.close()
    return [dict(r) for r in results]


def get_bio_summary() -> dict:
    """Return the current VALET-generated user profile summary, with timestamp.

    Returns {"summary": str, "updated": str_or_empty}. Empty summary if never generated.
    """
    db = _get_db()
    row = db.execute(
        "SELECT content, created_at FROM memories WHERE type='bio_summary' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        return {"summary": row["content"], "updated": row["created_at"] or ""}
    return {"summary": "", "updated": ""}


def set_bio_summary(content: str) -> None:
    """Replace the VALET-generated profile summary (single canonical entry, importance=10)."""
    db = _get_db()
    db.execute("DELETE FROM memories WHERE type='bio_summary'")
    if content.strip():
        db.execute(
            "INSERT INTO memories (content, type, source, importance) VALUES (?, 'bio_summary', 'valet-generated', 10)",
            (content.strip(),),
        )
    db.commit()


def get_bio_sources() -> list[dict]:
    """Return raw notes used as inputs to bio summary generation.

    Pulls voice-added bio notes plus high-importance facts about the user.
    Skips the summary itself so we don't feed it back into its own regeneration.
    """
    db = _get_db()
    rows = db.execute(
        "SELECT content, type, importance, created_at FROM memories "
        "WHERE type IN ('bio_note', 'fact') AND importance >= 7 "
        "ORDER BY importance DESC, id DESC LIMIT 100"
    ).fetchall()
    return [dict(r) for r in rows]


def add_bio_note(content: str) -> int:
    """Append an incremental fact about the user (importance=10, type='bio_note').

    These are the raw source notes VALET keeps. The bio_summary is a synthesis
    over these plus high-importance facts; it's regenerated on demand.
    """
    db = _get_db()
    cur = db.execute(
        "INSERT INTO memories (content, type, source, importance) VALUES (?, 'bio_note', 'voice', 10)",
        (content.strip(),),
    )
    db.commit()
    return cur.lastrowid


def get_recent_memories(limit: int = 10) -> list[dict]:
    """Get most recent memories."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


def get_important_memories(limit: int = 10) -> list[dict]:
    """Get highest importance memories."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM memories ORDER BY importance DESC, access_count DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def create_task(title: str, description: str = "", priority: str = "medium",
                due_date: str = "", due_time: str = "", project: str = "",
                tags: list[str] = None) -> int:
    """Create a task. Returns task ID."""
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO tasks (title, description, priority, due_date, due_time,
           project, tags, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, description, priority, due_date, due_time,
         project, json.dumps(tags or []), time.time())
    )
    task_id = cur.lastrowid
    conn.execute(
        "INSERT INTO task_fts (rowid, title, description, project, notes) VALUES (?, ?, ?, ?, ?)",
        (task_id, title, description, project, "")
    )
    conn.commit()
    conn.close()
    log.info(f"Created task [{priority}]: {title}")
    return task_id


def get_open_tasks(project: str = None) -> list[dict]:
    """Get all open/in-progress tasks, optionally filtered by project."""
    conn = _get_db()
    if project:
        results = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress') AND project LIKE ? ORDER BY "
            "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date",
            (f"%{project}%",)
        ).fetchall()
    else:
        results = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress') ORDER BY "
            "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date"
        ).fetchall()
    conn.close()
    return [dict(r) for r in results]


def get_tasks_for_date(date_str: str) -> list[dict]:
    """Get tasks due on a specific date (YYYY-MM-DD)."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM tasks WHERE due_date = ? AND status != 'cancelled' ORDER BY "
        "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_time",
        (date_str,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


def complete_task(task_id: int):
    """Mark a task as done."""
    conn = _get_db()
    conn.execute(
        "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
        (time.time(), task_id)
    )
    conn.commit()
    conn.close()


def search_tasks(query: str, limit: int = 10) -> list[dict]:
    """Search tasks by text."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = _get_db()
    try:
        results = conn.execute("""
            SELECT t.* FROM task_fts f
            JOIN tasks t ON f.rowid = t.id
            WHERE task_fts MATCH ?
            ORDER BY rank LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        results = []
    conn.close()
    return [dict(r) for r in results]


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def create_note(content: str, title: str = "", topic: str = "", tags: list[str] = None) -> int:
    """Create a note. Returns note ID."""
    conn = _get_db()
    now = time.time()
    cur = conn.execute(
        "INSERT INTO notes (title, content, topic, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (title, content, topic, json.dumps(tags or []), now, now)
    )
    note_id = cur.lastrowid
    conn.execute(
        "INSERT INTO note_fts (rowid, title, content, topic) VALUES (?, ?, ?, ?)",
        (note_id, title, content, topic)
    )
    conn.commit()
    conn.close()
    log.info(f"Created note: {title or content[:40]}")
    return note_id


def search_notes(query: str, limit: int = 10) -> list[dict]:
    """Search notes by text."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = _get_db()
    try:
        results = conn.execute("""
            SELECT n.* FROM note_fts f
            JOIN notes n ON f.rowid = n.id
            WHERE note_fts MATCH ?
            ORDER BY rank LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        results = []
    conn.close()
    return [dict(r) for r in results]


def get_notes_by_topic(topic: str) -> list[dict]:
    """Get all notes for a topic/project."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM notes WHERE topic LIKE ? ORDER BY updated_at DESC",
        (f"%{topic}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


# ---------------------------------------------------------------------------
# Context Builder — smart context for LLM calls
# ---------------------------------------------------------------------------

def build_memory_context(user_message: str) -> str:
    """Build relevant context from memories, tasks, and notes for the LLM.

    Searches for relevant memories based on what the user is talking about.
    Fast — runs FTS queries, no heavy computation.
    """
    parts = []

    # Always include: open high-priority tasks
    high_tasks = [t for t in get_open_tasks() if t["priority"] == "high"]
    if high_tasks:
        task_lines = [f"  - [{t['priority']}] {t['title']}" +
                      (f" (due {t['due_date']})" if t["due_date"] else "")
                      for t in high_tasks[:5]]
        parts.append("HIGH PRIORITY TASKS:\n" + "\n".join(task_lines))

    # Search memories relevant to what user is saying
    if len(user_message) > 5:
        relevant = recall(user_message, limit=3)
        if relevant:
            mem_lines = [f"  - [{m['type']}] {m['content']}" for m in relevant]
            parts.append("RELEVANT MEMORIES:\n" + "\n".join(mem_lines))

    # Recent important memories (always available)
    important = get_important_memories(limit=3)
    if important:
        imp_lines = [f"  - {m['content']}" for m in important
                     if not any(m["content"] == r["content"] for r in (relevant if 'relevant' in dir() else []))]
        if imp_lines:
            parts.append("KEY FACTS:\n" + "\n".join(imp_lines[:3]))

    return "\n\n".join(parts) if parts else ""


def format_tasks_for_voice(tasks: list[dict]) -> str:
    """Format tasks for voice response."""
    if not tasks:
        return "No tasks on the list, sir."
    count = len(tasks)
    high = [t for t in tasks if t["priority"] == "high"]
    if count == 1:
        t = tasks[0]
        return f"One task: {t['title']}." + (f" Due {t['due_date']}." if t["due_date"] else "")
    result = f"You have {count} open tasks."
    if high:
        result += f" {len(high)} are high priority."
    top = tasks[:3]
    for t in top:
        result += f" {t['title']}."
    if count > 3:
        result += f" And {count - 3} more."
    return result


def format_plan_for_voice(tasks: list[dict], events: list[dict]) -> str:
    """Format a day plan combining tasks and calendar events."""
    if not tasks and not events:
        return "Your day looks clear, sir. No events or tasks scheduled."

    parts = []
    if events:
        parts.append(f"{len(events)} events on the calendar")
    if tasks:
        high = [t for t in tasks if t["priority"] == "high"]
        parts.append(f"{len(tasks)} tasks" + (f", {len(high)} high priority" if high else ""))

    result = f"For tomorrow: {', '.join(parts)}. "

    # List events first
    if events:
        for e in events[:3]:
            result += f"{e.get('start', '')} {e['title']}. "

    # Then high priority tasks
    if tasks:
        for t in [t for t in tasks if t["priority"] == "high"][:2]:
            result += f"Priority: {t['title']}. "

    result += "Shall I adjust anything?"
    return result


# ---------------------------------------------------------------------------
# Memory extraction — learn from conversations
# ---------------------------------------------------------------------------

# Throttle state for extract_memories — see function docstring.
_last_extraction_ts: float = 0.0
_pending_turns: list[tuple[str, str]] = []
_EXTRACTION_MIN_INTERVAL_S = 30.0
_PENDING_MAX = 8


async def extract_memories(user_text: str, valet_response: str, anthropic_client) -> list[str]:
    """After a conversation turn, extract any facts worth remembering.

    Throttled to one Anthropic call per 30s — during rapid conversation we
    queue turns and process them in batch on the next eligible call. This
    halves API traffic during active use, sidestepping the 429 rate-limit
    backoff that was making VALET feel slow.
    """
    global _last_extraction_ts, _pending_turns

    if not anthropic_client or len(user_text) < 15:
        return []

    # Always queue this turn so it gets processed eventually.
    _pending_turns.append((user_text, valet_response))
    if len(_pending_turns) > _PENDING_MAX:
        # Drop oldest if queue overflows — these are low-value casual chats.
        _pending_turns = _pending_turns[-_PENDING_MAX:]

    import time as _time
    now = _time.time()
    if now - _last_extraction_ts < _EXTRACTION_MIN_INTERVAL_S:
        return []  # within throttle window — let later turn process the queue

    _last_extraction_ts = now
    batch = _pending_turns[:]
    _pending_turns = []
    convo_block = "\n\n".join(f"User: {u}\nVALET: {j}" for u, j in batch)

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=(
                "Extract facts worth remembering from this conversation. "
                "Only extract CONCRETE facts: preferences, decisions, names, dates, plans, goals. "
                "NOT opinions, greetings, or casual chat. "
                "Return JSON array of objects: [{\"type\": \"fact|preference|project|person|decision\", \"content\": \"...\", \"importance\": 1-10}] "
                "Return [] if nothing worth remembering. Be very selective."
            ),
            messages=[{"role": "user", "content": convo_block}],
        )

        text = response.content[0].text.strip()
        # Parse JSON
        if text.startswith("["):
            items = json.loads(text)
            stored = []
            for item in items:
                if isinstance(item, dict) and "content" in item:
                    remember(
                        content=item["content"],
                        mem_type=item.get("type", "fact"),
                        source=user_text[:50],
                        importance=item.get("importance", 5),
                    )
                    stored.append(item["content"])
            return stored
    except Exception as e:
        log.debug(f"Memory extraction failed: {e}")

    return []


# Initialize on import
init_db()
