"""
JARVIS Design Partner — conversational pre-flight for Claude Code dispatch.

The user talks out loud about a feature they want to build. Opus shapes what
they're describing into a prompt clear enough to hand to a `claude` subprocess
in Phase 4. State machine per WS connection:

  IDLE      → "let's design X" → DESIGNING
  DESIGNING → "ship it"        → BUILDING (Phase 4 owns the handoff)
  DESIGNING → "scrap this"     → IDLE

handle_turn() calls Opus with forced design_turn tool-use; applies the returned
patch to the running draft and emits design.* events directly to the originating
WebSocket (Design Panel subscribes; Process Panel ignores).

History is a SNAPSHOT of voice history at session start — see plan.md, the
"three-Opus-loops" decision — so JARVIS chit-chat doesn't bleed into design
calls. The design session keeps its own message log from there.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

log = logging.getLogger("jarvis.design_partner")

SessionState = Literal["IDLE", "DESIGNING", "BUILDING"]


# ---------------------------------------------------------------------------
# System prompt + tool schema (the Phase 3 review checkpoint — locked once
# approved, edit deliberately).
# ---------------------------------------------------------------------------

_DESIGN_SYSTEM_PROMPT = """You are the design partner inside JARVIS, a voice-first macOS assistant. The
user is talking out loud about a feature they want to build in {project_name}.
Your job is to help them shape what they're describing into a prompt clear
enough to hand to Claude Code — not to build it yourself.

You ALWAYS reply through the design_turn tool. Never reply in free text.

VOICE REPLY:
- Up to 5 sentences for genuine choice-points or option-laying. Default to
  1-2 for acks and clarifications. British butler tone. Dry, economical,
  never effusive.
- No markdown, no lists, no "I" sentences ("Will do, sir" / "Right away" /
  "Noted" / "Push back: …" / "Worth pinning down: …").
- No reading back what they just said. Move the conversation forward.

PANEL UPDATES (panel_delta):
- decision: a concrete choice the conversation just settled. ("New file:
  daily_rollup.py, not added to server.py.")
- question: a clarifying question you want pinned in the timeline. Surface
  AT MOST ONE per turn — voice already speaks it.
- assumption: something you're treating as true that the user hasn't
  confirmed. Be explicit so they can correct it. ("Assuming a daily cron,
  not on-demand.")
- context: a file you're pulling into the conversation. The user can click
  to see what you read.

DRAFT PROMPT (draft_patch):
- Only emit fields that CHANGED this turn. Empty patches are fine.
- goal: one-paragraph statement of what the feature does.
- context: what existing code/files the implementer needs to know about.
- constraints: technical or product requirements (must, must-not).
- acceptance: how we'll know it's done.
- surfaced_files: explicit file paths to include in the handoff prompt.
- open_questions: things still unresolved at ship time.

BEHAVIOR:
- Push back on ambiguity. If the user says "make it real-time", ask what
  latency budget. If they say "users", ask which kind.
- Surface assumptions explicitly. Don't silently pick.
- Ask ONE question at a time — don't pile up.
- When the draft is concrete enough to hand off, say so plainly:
  "Ready when you are, sir." Set ready_to_ship=true. The user decides
  when to ship.

GREENFIELD MODE:
When the session is for a brand-new project (no existing code yet), your
FIRST turn must ask about the stack. Specifically the language and the
framework. Capture the answer via the stack_pick field of the design_turn
tool (language: python/node/go/rust/other, framework: free-form string).
Don't push deeply on architecture before you know what runtime they want.
After stack is captured, design normally. Greenfield drafts should include
a clear goal, the chosen stack in context, and acceptance criteria the
first ship can be measured against.

WARM CONTEXT:
You have the project's CLAUDE.md, README, file tree, recent commits, and a
few entry points already loaded. Treat that as ground truth. If you need
something more, surface it via panel_delta type="context" — the user sees
what you read. For greenfield projects there is no warm context; rely on
the user's words.

NEVER FABRICATE:
- Don't invent files, functions, or classes that the warm context doesn't
  show. If you're not sure something exists, ASK or surface the file via
  panel_delta.
- Don't reference projects, conversations, or decisions from prior sessions
  unless they appear in the warm context for THIS project.
"""


_DESIGN_TOOL = {
    "name": "design_turn",
    "description": "Respond to the user's design-conversation turn. ALWAYS use this tool — never reply in free text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "voice_reply": {
                "type": "string",
                "description": "1-5 sentence British-butler reply for TTS. No markdown. No 'I' sentences.",
            },
            "panel_delta": {
                "type": "array",
                "description": "Entries to add to the timeline this turn.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type":   {"type": "string", "enum": ["decision", "question", "assumption", "context"]},
                        "title":  {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["type", "title"],
                },
            },
            "draft_patch": {
                "type": "object",
                "description": "Only fields that changed this turn. Empty is fine.",
                "properties": {
                    "goal":            {"type": "string"},
                    "context":         {"type": "string"},
                    "constraints":     {"type": "string"},
                    "acceptance":      {"type": "string"},
                    "surfaced_files":  {"type": "array", "items": {"type": "string"}},
                    "open_questions":  {"type": "array", "items": {"type": "string"}},
                },
            },
            "ready_to_ship": {
                "type": "boolean",
                "description": "True if the draft is concrete enough to hand off. Affects panel rendering; does NOT auto-ship.",
            },
            "stack_pick": {
                "type": "object",
                "description": "Greenfield only — the language + framework the user just confirmed. Emit once when captured; subsequent turns can omit.",
                "properties": {
                    "language":  {"type": "string", "enum": ["python", "node", "go", "rust", "other"]},
                    "framework": {"type": "string"},
                },
            },
        },
        "required": ["voice_reply"],
    },
}


# ---------------------------------------------------------------------------
# DraftPrompt
# ---------------------------------------------------------------------------

@dataclass
class DraftPrompt:
    goal: str = ""
    context: str = ""
    constraints: str = ""
    acceptance: str = ""
    surfaced_files: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def apply_patch(self, patch: dict) -> list[str]:
        """Apply a partial patch in place. Returns list of field names that changed."""
        changed: list[str] = []
        for key in ("goal", "context", "constraints", "acceptance"):
            if key in patch and patch[key] != getattr(self, key):
                setattr(self, key, patch[key])
                changed.append(key)
        for key in ("surfaced_files", "open_questions"):
            if key in patch:
                cur = getattr(self, key)
                # Append-only for lists — accumulate rather than replace so
                # earlier-turn entries persist.
                new_items = [x for x in patch[key] if x not in cur]
                if new_items:
                    setattr(self, key, cur + new_items)
                    changed.append(key)
        return changed

    def is_empty(self) -> bool:
        return not any([
            self.goal, self.context, self.constraints, self.acceptance,
            self.surfaced_files, self.open_questions,
        ])

    def render_markdown(self) -> str:
        """Markdown rendering of the assembled draft for the panel + handoff."""
        if self.is_empty():
            return "_(draft is empty — keep designing)_"
        parts = []
        if self.goal:
            parts.append(f"## Goal\n{self.goal.strip()}")
        if self.context:
            parts.append(f"## Context\n{self.context.strip()}")
        if self.constraints:
            parts.append(f"## Constraints\n{self.constraints.strip()}")
        if self.acceptance:
            parts.append(f"## Acceptance criteria\n{self.acceptance.strip()}")
        if self.surfaced_files:
            files = "\n".join(f"- `{f}`" for f in self.surfaced_files)
            parts.append(f"## Files to reference\n{files}")
        if self.open_questions:
            qs = "\n".join(f"- {q}" for q in self.open_questions)
            parts.append(f"## Open questions\n{qs}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# DesignSession
# ---------------------------------------------------------------------------

def _git_branch_for(path: Path) -> Optional[str]:
    """Read the current branch name from a project's .git/HEAD. Best-effort.

    Returns None if no .git/HEAD or unparseable; short SHA for detached HEAD.
    Cheap — single file read, no subprocess.
    """
    try:
        head_file = path / ".git" / "HEAD"
        if not head_file.exists():
            return None
        content = head_file.read_text().strip()
        if content.startswith("ref: refs/heads/"):
            return content.replace("ref: refs/heads/", "")
        return content[:8]  # detached HEAD
    except Exception:
        return None


@dataclass
class DesignSession:
    id: str
    project_path: Optional[Path]                       # None when no project is open
    topic: str
    ws: Any                                            # WebSocket — used to emit design events
    state: SessionState = "DESIGNING"
    history: list[dict] = field(default_factory=list)  # Opus conversation log (NOT the voice history)
    draft: DraftPrompt = field(default_factory=DraftPrompt)
    ready_to_ship: bool = False
    self_mod: bool = False
    started_at: float = field(default_factory=time.time)
    # Phase 5 self-mod metadata — populated after the approval gate creates
    # the feature branch. None on non-self-mod sessions.
    feature_branch: Optional[str] = None
    pre_build_sha: Optional[str] = None

    # Sub-agent dispatch — name of the Claude Code agent the ship-it prompt
    # should route to. None / "" means no explicit pick (or "auto" — server
    # then runs agents.auto_detect_agent at ship time).
    agent: Optional[str] = None

    # Greenfield mode — the user is designing a brand-new project, not
    # editing an existing one. When new_project_name is set, ship-it
    # scaffolds the directory + git-inits + commits + opens Cursor before
    # pasting the prompt. project_path stays None until the scaffold runs.
    new_project_name: Optional[str] = None
    new_project_base_dir: Optional[str] = None

    # Stack pick (greenfield) — captured by the design partner's stack
    # question on the first greenfield turn. Drives the .gitignore template
    # + the manifest file new_cursor_project scaffolds before first ship.
    # Shape: {"language": "python"|"node"|"go"|"rust"|"other", "framework": "fastapi"|"react"|"vite"|...|""}.
    stack: Optional[dict] = None

    @property
    def project_name(self) -> str:
        return self.project_path.name if self.project_path else ""

    @property
    def has_target(self) -> bool:
        # Greenfield (new_project_name set) counts as having a target even
        # though project_path is None until the scaffold runs at ship time.
        return self.project_path is not None or bool(self.new_project_name)

    @property
    def is_greenfield(self) -> bool:
        return self.project_path is None and bool(self.new_project_name)

    @property
    def git_branch(self) -> Optional[str]:
        return _git_branch_for(self.project_path) if self.project_path else None

    async def emit(self, event_type: str, title: str = "", detail: str = "",
                   status: str = "active", payload: Optional[dict] = None) -> None:
        """Send a design.* event to the originating WS for the Design Panel."""
        event = {
            "type": event_type,
            "session_id": self.id,
            "title": title,
            "detail": detail,
            "status": status,
            "timestamp": time.time(),
            "payload": payload or {},
        }
        try:
            await self.ws.send_json({"type": "design_event", "event": event})
        except Exception as e:
            log.warning(f"design_event send failed: {e}")

    async def emit_state(self) -> None:
        """Push current state + draft + target + ready_to_ship to the panel.

        Includes project_name / project_path / git_branch so the panel can
        render the ship target row from the same source of truth as the
        Phase 4 ship-it handoff (they read the same `session.project_path`).
        """
        await self.emit(
            "design.state_changed",
            title=self.state,
            status="done",
            payload={
                "state": self.state,
                "ready_to_ship": self.ready_to_ship,
                "topic": self.topic,
                "self_mod": self.self_mod,
                "draft_markdown": self.draft.render_markdown(),
                "draft": asdict(self.draft),
                "project_name": self.project_name,
                "project_path": str(self.project_path) if self.project_path else "",
                "has_target": self.has_target,
                "git_branch": self.git_branch or "",
                "agent": self.agent or "",
                "new_project_name": self.new_project_name or "",
                "new_project_base_dir": self.new_project_base_dir or "",
                "is_greenfield": self.is_greenfield,
                "stack": self.stack or {},
            },
        )

    async def handle_turn(self, user_text: str, anthropic_client) -> str:
        """One conversational turn. Returns voice_reply string for TTS.

        Calls Opus with forced design_turn tool-use, applies the returned
        patch to the running draft, emits timeline events to the panel.

        If project_path is None (no project open at session start), Opus
        designs abstractly — useful for sketching before assigning a target,
        but the user must open a project before ship-it succeeds.
        """
        from project_context import get as get_warm
        if self.project_path is None:
            warm_block = (
                "# Project: (none)\n"
                "No project is currently open. Design abstractly. The user must "
                "open a project before shipping; their ship button is disabled "
                "until then. Prefer concrete file names/paths from prior project "
                "conventions only when the user names a specific project."
            )
            project_token = "(no project yet)"
        else:
            warm = get_warm(self.project_path)
            warm_block = warm.summary_for_prompt() if warm else f"# Project: {self.project_path.name}\n(no warm context loaded)"
            project_token = self.project_path.name

        system = _DESIGN_SYSTEM_PROMPT.replace("{project_name}", project_token)
        system += "\n\n# WARM CONTEXT\n" + warm_block
        system += f"\n\n# CURRENT DRAFT\n{self.draft.render_markdown()}"

        # Append user turn to history before the call so Opus sees it.
        self.history.append({"role": "user", "content": user_text})

        try:
            response = await anthropic_client.messages.create(
                # Temporary: Sonnet 4.6 while Opus 4.7 is returning 529
                # Overloaded (2026-05-18 Anthropic-side incident, see logs
                # around 18:00-18:04). Flip back to claude-opus-4-7 once
                # Opus recovers; Sonnet is fine as the design-partner
                # model — same skill family, slightly lower capability
                # for nuanced product reasoning but more than adequate
                # for the question/draft loop.
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=system,
                tools=[_DESIGN_TOOL],
                tool_choice={"type": "tool", "name": "design_turn"},
                messages=self.history,
            )
        except Exception as e:
            log.error(f"design_partner Opus call failed: {e}")
            # Roll back the user turn so retry doesn't double-add
            self.history.pop()
            return "I had trouble thinking that through, sir. Try again?"

        # Extract the tool_use block (forced; should always be present).
        tool_block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
        if not tool_block:
            log.warning("design_partner: no tool_use block in response")
            self.history.pop()
            return "I'm afraid I lost my train of thought, sir."

        result = tool_block.input
        voice_reply = result.get("voice_reply", "Noted, sir.")
        panel_delta = result.get("panel_delta", []) or []
        draft_patch = result.get("draft_patch", {}) or {}
        ready = bool(result.get("ready_to_ship", False))
        stack_pick = result.get("stack_pick") or None

        # Greenfield: capture the stack the moment Opus reports it. Persisted
        # on the session so _execute_ship_design can pass it to
        # new_cursor_project for stack-aware scaffolding.
        if stack_pick and isinstance(stack_pick, dict):
            lang = (stack_pick.get("language") or "").strip().lower()
            fw = (stack_pick.get("framework") or "").strip()
            if lang:
                self.stack = {"language": lang, "framework": fw}

        # Persist the tool-use turn + synthesize a tool_result so the next call
        # has a valid conversation continuation per Anthropic's tool-use protocol.
        self.history.append({
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": tool_block.id,
                "name": "design_turn",
                "input": result,
            }],
        })
        self.history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": "ok",
            }],
        })

        # Emit timeline entries.
        for entry in panel_delta:
            etype = entry.get("type", "decision")
            await self.emit(
                f"design.{etype}_added" if etype in ("assumption", "decision")
                else f"design.{etype}_asked" if etype == "question"
                else f"design.{etype}_surfaced",
                title=entry.get("title", ""),
                detail=entry.get("detail", ""),
                status="active" if etype == "question" else "done",
                payload={"entry": entry},
            )

        # Apply draft patch + emit if changed.
        changed = self.draft.apply_patch(draft_patch)
        if changed:
            await self.emit(
                "design.draft_updated",
                title="Draft updated",
                detail=", ".join(changed),
                status="done",
                payload={
                    "changed": changed,
                    "draft_markdown": self.draft.render_markdown(),
                    "draft": asdict(self.draft),
                },
            )

        # Push ready-to-ship transitions.
        if ready != self.ready_to_ship:
            self.ready_to_ship = ready
            await self.emit_state()

        return voice_reply

    def scrap(self) -> None:
        self.state = "IDLE"
        self.draft = DraftPrompt()
        self.ready_to_ship = False
        self.history = []

    def mark_building(self) -> None:
        self.state = "BUILDING"


# ---------------------------------------------------------------------------
# Per-WebSocket session registry. One active design session per connection.
# ---------------------------------------------------------------------------

_active: dict[int, DesignSession] = {}


def get_for_ws(ws) -> Optional[DesignSession]:
    return _active.get(id(ws))


def start_for_ws(ws, project_path: Optional[Path], topic: str, self_mod: bool = False) -> DesignSession:
    """Create + register a fresh design session for this WebSocket.

    `project_path` may be None when no project is currently open; the session
    starts anyway in "no target" mode (ship disabled, abstract design).
    Stops any prior session on the same WS.
    """
    stop_for_ws(ws)
    session = DesignSession(
        id=str(uuid.uuid4())[:8],
        project_path=Path(project_path) if project_path else None,
        topic=topic,
        ws=ws,
        self_mod=self_mod,
    )
    _active[id(ws)] = session
    return session


def stop_for_ws(ws) -> None:
    _active.pop(id(ws), None)


# ---------------------------------------------------------------------------
# SQLite persistence (write-only audit trail for shipped/scrapped sessions).
# ---------------------------------------------------------------------------

def persist(session: DesignSession, status: str, final_prompt: str = "",
            ship_method: str = "", inbox_path: str = "") -> None:
    """Write a design_sessions row. Idempotent via INSERT OR REPLACE on session id."""
    from memory import _get_db
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO design_sessions
           (id, topic, project_path, started_at, finished_at, final_prompt, status, self_mod, ship_method, inbox_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session.id,
            session.topic,
            str(session.project_path) if session.project_path else "",
            session.started_at,
            time.time(),
            final_prompt,
            status,
            int(session.self_mod),
            ship_method,
            inbox_path,
        ),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Phase 4 — ship-it handoff
# ---------------------------------------------------------------------------

def compose_final_prompt(session: DesignSession) -> str:
    """Assemble the prompt to hand to Claude Code.

    Order (deliberate — matches what the Design Panel's draft pane has been
    showing, so nothing in the shipped prompt surprises the user):

      1. ## Task: <topic>
      2. The DraftPrompt body (goal/context/constraints/acceptance/files/Qs).
      3. ## Project warm context — from project_context.summary_for_prompt().
      4. ## Surfaced files — explicit paths the user surfaced during design,
         resolved to absolute paths under project_path when possible.
    """
    from project_context import get as get_warm

    parts: list[str] = [f"## Task: {session.topic}", ""]
    parts.append(session.draft.render_markdown())

    if session.project_path:
        warm = get_warm(session.project_path)
        if warm:
            parts.append("\n## Project warm context\n")
            parts.append(warm.summary_for_prompt())

    if session.draft.surfaced_files and session.project_path:
        parts.append("\n## Surfaced file paths (absolute)\n")
        for f in session.draft.surfaced_files:
            p = Path(f)
            if not p.is_absolute() and session.project_path:
                p = session.project_path / f
            parts.append(f"- `{p}`")

    return "\n".join(parts).strip() + "\n"


def ship_via_file(session: DesignSession, final_prompt: str) -> Path:
    """Method A — write the composed prompt to <project>/.jarvis/inbox/<id>.md.

    Returns the resolved file path. Caller is expected to surface the path
    via voice + panel ("Prompt staged at … — paste into Cursor's claude
    terminal to ship."). Safe, deterministic, no AppleScript fragility.
    """
    if not session.project_path:
        raise ValueError("ship_via_file: session has no project_path")

    inbox = session.project_path / ".jarvis" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    out = inbox / f"{session.id}.md"
    header = (
        f"<!-- JARVIS Design Session {session.id} — {session.topic}\n"
        f"     Shipped at {time.strftime('%Y-%m-%d %H:%M:%S')} via ship_method=file\n"
        f"     Paste this file's body into Cursor's claude terminal to dispatch. -->\n\n"
    )
    out.write_text(header + final_prompt)
    log.info(f"ship_via_file: wrote {out}")
    return out


async def ship_via_applescript(session: DesignSession, final_prompt: str) -> bool:
    """Method B — clipboard-paste into Cursor's frontmost terminal pane.

    Brittle by design: assumes the user has focus on Cursor's claude terminal
    when JARVIS triggers the paste. Caller is expected to have asked for
    explicit voice confirmation ('ship it for real') before reaching here.

    Returns True on AppleScript success, False otherwise. Does NOT verify
    that the prompt actually landed in the right pane (Cursor's Electron
    DOM isn't introspectable from System Events).
    """
    import asyncio as _asyncio
    # Place on clipboard via pbcopy (handles newlines + quoting cleanly).
    proc = await _asyncio.create_subprocess_exec(
        "pbcopy",
        stdin=_asyncio.subprocess.PIPE,
    )
    await proc.communicate(input=final_prompt.encode())
    if proc.returncode != 0:
        log.warning("ship_via_applescript: pbcopy failed")
        return False

    # Bring Cursor to front, paste, send return.
    script = '''
    tell application "Cursor" to activate
    delay 0.6
    tell application "System Events"
        keystroke "v" using command down
        delay 0.2
        key code 36
    end tell
    '''
    proc = await _asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(f"ship_via_applescript: osascript failed: {stderr.decode()[:200]}")
        return False
    return True


def get_ship_method() -> str:
    """Read configured ship method. Default 'file'.

    Valid: 'file' | 'applescript' | 'auto_paste'.

    'auto_paste' (chunk 21) is the primary voice→Cursor path: pre-flight
    checks Cursor is frontmost, then clipboard-pastes + presses Enter via
    System Events. Pre-flight failure falls back to 'file' inside
    _execute_ship_design. See docs/auto_paste_diagnosis.md (chunk 22) for
    the bug history — the validator silently coerced unknown values for
    one commit, masking the entire auto_paste path.
    """
    try:
        from actions import _design_partner_config
        cfg = _design_partner_config()
        m = cfg.get("ship_method", "file")
        return m if m in ("file", "applescript", "auto_paste") else "file"
    except Exception:
        return "file"
