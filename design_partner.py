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

WARM CONTEXT:
You have the project's CLAUDE.md, README, file tree, recent commits, and a
few entry points already loaded. Treat that as ground truth. If you need
something more, surface it via panel_delta type="context" — the user sees
what you read.

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

@dataclass
class DesignSession:
    id: str
    project_path: Path
    topic: str
    ws: Any                                            # WebSocket — used to emit design events
    state: SessionState = "DESIGNING"
    history: list[dict] = field(default_factory=list)  # Opus conversation log (NOT the voice history)
    draft: DraftPrompt = field(default_factory=DraftPrompt)
    ready_to_ship: bool = False
    self_mod: bool = False
    started_at: float = field(default_factory=time.time)

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
        """Push current state + draft + ready_to_ship to the panel."""
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
            },
        )

    async def handle_turn(self, user_text: str, anthropic_client) -> str:
        """One conversational turn. Returns voice_reply string for TTS.

        Calls Opus with forced design_turn tool-use, applies the returned
        patch to the running draft, emits timeline events to the panel.
        """
        # Build system prompt: static text + project-specific warm context + draft snapshot.
        from project_context import get as get_warm
        warm = get_warm(self.project_path)
        warm_block = warm.summary_for_prompt() if warm else f"# Project: {self.project_path.name}\n(no warm context loaded)"

        system = _DESIGN_SYSTEM_PROMPT.replace("{project_name}", self.project_path.name)
        system += "\n\n# WARM CONTEXT\n" + warm_block
        system += f"\n\n# CURRENT DRAFT\n{self.draft.render_markdown()}"

        # Append user turn to history before the call so Opus sees it.
        self.history.append({"role": "user", "content": user_text})

        try:
            response = await anthropic_client.messages.create(
                model="claude-opus-4-7",
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


def start_for_ws(ws, project_path: Path, topic: str, self_mod: bool = False) -> DesignSession:
    """Create + register a fresh design session for this WebSocket.

    Stops any prior session on the same WS (rare — would mean "let's design Y"
    while already in DESIGNING for X without a scrap/ship in between).
    """
    stop_for_ws(ws)
    session = DesignSession(
        id=str(uuid.uuid4())[:8],
        project_path=Path(project_path),
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

def persist(session: DesignSession, status: str, final_prompt: str = "") -> None:
    """Write a design_sessions row. Idempotent via INSERT OR REPLACE on session id."""
    from memory import _get_db
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO design_sessions
           (id, topic, project_path, started_at, finished_at, final_prompt, status, self_mod)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session.id,
            session.topic,
            str(session.project_path),
            session.started_at,
            time.time(),
            final_prompt,
            status,
            int(session.self_mod),
        ),
    )
    conn.commit()
    conn.close()
