"""
VALET Work Mode — persistent claude -p sessions tied to projects.

VALET can connect to any project directory and maintain a conversation
with Claude Code. Uses --continue to resume the most recent session
in that directory, so context persists across messages.

The user sees Claude Code working in their Terminal window.
VALET reads the responses via subprocess, summarizes, and reports back.
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from process_events import emit_code_task, emit_tool_event

log = logging.getLogger("valet.work_mode")

# --- Stream-json parsing --------------------------------------------------
#
# `claude -p --output-format stream-json --verbose` emits one JSON object
# per line. We map the structured events to typed `tool.*` process-panel
# events so the panel renders a vertical list of intermediate tool calls
# instead of opaque "claude working…" output.
#
# Any line that is NOT valid JSON, or any event type we don't recognize,
# falls through to the existing emit_code_task path — preserves the text
# fallback and protects against Claude Code format drift. (See the
# parse_stream_line unit test at the bottom of this file.)

# Map Claude Code tool names to (event_type, parameter_extractor) tuples.
# Each extractor takes the tool input dict and returns a short human-readable
# string (≤60 chars handled by the panel renderer).
def _read_param(inp: dict) -> str:
    return _short_path(inp.get("file_path", ""))
def _write_param(inp: dict) -> str:
    return _short_path(inp.get("file_path", ""))
def _edit_param(inp: dict) -> str:
    return _short_path(inp.get("file_path", ""))
def _bash_param(inp: dict) -> str:
    cmd = inp.get("command", "")
    return cmd if len(cmd) < 80 else cmd[:77] + "…"
def _web_search_param(inp: dict) -> str:
    return str(inp.get("query", ""))
def _web_fetch_param(inp: dict) -> str:
    return str(inp.get("url", ""))
def _glob_param(inp: dict) -> str:
    return str(inp.get("pattern", ""))
def _grep_param(inp: dict) -> str:
    p = inp.get("pattern", "")
    path = inp.get("path", "")
    return f"{p}  in  {_short_path(path)}" if path else p
def _task_param(inp: dict) -> str:
    return str(inp.get("description") or inp.get("subagent_type") or "subagent")

_TOOL_MAP: dict[str, tuple[str, Any]] = {
    "Read":      ("tool.file_read",  _read_param),
    "Write":     ("tool.file_write", _write_param),
    "Edit":      ("tool.file_edit",  _edit_param),
    "Bash":      ("tool.bash",       _bash_param),
    "WebSearch": ("tool.web_search", _web_search_param),
    "WebFetch":  ("tool.web_fetch",  _web_fetch_param),
    "Glob":      ("tool.glob",       _glob_param),
    "Grep":      ("tool.grep",       _grep_param),
    "Task":      ("tool.task",       _task_param),
}


def _short_path(p: str) -> str:
    """Compact a file path: prefer basename, prepend parent dir when ambiguous."""
    if not p:
        return ""
    parts = p.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1]


def parse_stream_line(line: str) -> list[dict]:
    """Parse a single stream-json line, return list of structured panel events.

    Each event: {"event_type": "tool.X", "title": str, "detail": str, "payload": {...}}.
    Empty list = nothing to surface (unknown event type or non-event line).
    Returns [] (no exception) on parse failure — caller falls back to text.
    """
    s = line.strip()
    if not s:
        return []
    try:
        obj = json.loads(s)
    except Exception:
        return []  # Caller falls back to emit_code_task

    if not isinstance(obj, dict):
        return []

    t = obj.get("type")
    events: list[dict] = []

    if t == "assistant":
        for block in (obj.get("message") or {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "tool_use":
                name = block.get("name", "")
                inp = block.get("input", {}) or {}
                event_type, extractor = _TOOL_MAP.get(name, (f"tool.{name.lower()}", lambda _i: ""))
                try:
                    detail = extractor(inp)
                except Exception:
                    detail = ""
                events.append({
                    "event_type": event_type,
                    "title": name,
                    "detail": detail,
                    "payload": {"input": inp},
                })
            elif bt == "thinking":
                thinking = (block.get("thinking") or "").strip()
                if thinking:
                    events.append({
                        "event_type": "tool.thinking",
                        "title": "thinking",
                        "detail": thinking[:80] + ("…" if len(thinking) > 80 else ""),
                        "payload": {"text": thinking},
                    })
            # 'text' blocks are the assistant's free reply — kept out of the
            # tool stream; caller surfaces those via emit_code_task fallback.

    elif t == "result":
        # Terminal summary — duration + cost. Useful as a panel footer.
        cost = obj.get("total_cost_usd")
        ms = obj.get("duration_ms")
        events.append({
            "event_type": "tool.result",
            "title": "claude done",
            "detail": f"{ms}ms" + (f", ${cost:.4f}" if isinstance(cost, (int, float)) else ""),
            "payload": {"duration_ms": ms, "cost_usd": cost},
        })

    # system / rate_limit_event / user / unknown → no panel event, no error.
    return events

SESSION_FILE = Path(__file__).parent / "data" / "active_session.json"


class WorkSession:
    """A claude -p session tied to a project directory.

    Each project gets its own session. VALET can switch between projects
    and --continue picks up where the last message left off.
    """

    def __init__(self):
        self._active = False
        self._working_dir: str | None = None
        self._project_name: str | None = None
        self._message_count = 0  # Track if this is first message (no --continue)
        self._status = "idle"  # idle, working, done

    @property
    def active(self) -> bool:
        return self._active

    @property
    def project_name(self) -> str | None:
        return self._project_name

    @property
    def status(self) -> str:
        return self._status

    async def start(self, working_dir: str, project_name: str = None):
        """Start or switch to a project session."""
        self._working_dir = working_dir
        self._project_name = project_name or working_dir.split("/")[-1]
        self._active = True
        self._message_count = 0
        self._status = "idle"
        log.info(f"Work mode started: {self._project_name} ({working_dir})")

    async def send(self, user_text: str, task_id: str | None = None,
                   anthropic_client: Any = None, resume: bool = False) -> str:
        """Send a message to claude -p and get the full response.

        First message in a session: fresh claude -p
        Subsequent messages: claude -p --continue (resumes last session in dir)
        resume=True forces --continue even on a fresh WorkSession, so a separate
        dispatch can pick up the last Claude Code session left in that directory.

        If `task_id` is given, stdout is streamed as tool.* events on the
        process bus (plus fallback code_task for unparsed lines).

        If both `task_id` AND `anthropic_client` are given, runs the
        post-completion Haiku middleware to extract structured result cards
        (web / product / location / image) from the response and tool
        results — emits result.* events for the panel.
        """
        claude_path = shutil.which("claude")
        if not claude_path:
            return "Claude CLI not found on this system."

        cmd = [
            claude_path, "-p",
            "--output-format", "stream-json",
            "--verbose",                       # required with stream-json under -p
            "--dangerously-skip-permissions",
        ]

        # Use --continue for subsequent messages to maintain context (or when a
        # caller explicitly resumes the last session left in this directory).
        if self._message_count > 0 or resume:
            cmd.append("--continue")

        self._status = "working"

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir,
            )

            collected_lines: list[str] = []
            assistant_text_parts: list[str] = []
            tool_result_snippets: list[str] = []

            async def _drain_stdout():
                """Read stream-json line-by-line. For each line:
                  - Parse → typed tool.* events if recognized.
                  - Extract assistant text blocks into the response buffer.
                  - Unrecognized / non-JSON → fall through to emit_code_task
                    so nothing is ever lost from the panel."""
                assert process.stdout is not None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace").rstrip("\n")
                    collected_lines.append(decoded)
                    if not decoded:
                        continue

                    # Try structured parse first.
                    events = parse_stream_line(decoded)

                    # Extract assistant.text blocks (canonical response) AND
                    # user.tool_result snippets (middleware context).
                    try:
                        obj = json.loads(decoded)
                        if isinstance(obj, dict):
                            if obj.get("type") == "assistant":
                                for blk in (obj.get("message") or {}).get("content", []) or []:
                                    if isinstance(blk, dict) and blk.get("type") == "text":
                                        txt = blk.get("text", "")
                                        if txt:
                                            assistant_text_parts.append(txt)
                            elif obj.get("type") == "user":
                                msg = obj.get("message") or {}
                                content = msg.get("content")
                                if isinstance(content, list):
                                    for blk in content:
                                        if isinstance(blk, dict) and blk.get("type") == "tool_result":
                                            c = blk.get("content", "")
                                            if isinstance(c, str) and c:
                                                tool_result_snippets.append(c)
                                            elif isinstance(c, list):
                                                # tool_result can be a list of content blocks
                                                for sub in c:
                                                    if isinstance(sub, dict) and sub.get("type") == "text":
                                                        tool_result_snippets.append(sub.get("text", ""))
                    except Exception:
                        pass

                    if not task_id:
                        continue

                    if events:
                        for ev in events:
                            try:
                                await emit_tool_event(
                                    task_id,
                                    ev["event_type"],
                                    ev["title"],
                                    detail=ev.get("detail", ""),
                                    payload=ev.get("payload") or {},
                                )
                            except Exception:
                                # Best-effort: emit failure must not crash the
                                # read loop or the claude subprocess.
                                pass
                    else:
                        # Fallback path: unknown line type OR non-JSON OR
                        # event types we don't surface yet. Keep raw text
                        # available so nothing's lost.
                        try:
                            await emit_code_task(task_id, decoded)
                        except Exception:
                            pass

            async def _write_stdin():
                assert process.stdin is not None
                try:
                    process.stdin.write(user_text.encode())
                    await process.stdin.drain()
                finally:
                    process.stdin.close()

            try:
                await asyncio.wait_for(
                    asyncio.gather(_write_stdin(), _drain_stdout(), process.wait()),
                    timeout=300,
                )
            except asyncio.TimeoutError:
                log.error("claude -p timed out after 300s")
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                self._status = "timeout"
                return "That's taking longer than expected, sir. The operation timed out."

            # Prefer the structured assistant.text concatenation as the
            # canonical response. Fall back to raw lines if no text blocks
            # were parsed (defensive against format drift).
            if assistant_text_parts:
                response = "".join(assistant_text_parts).strip()
            else:
                response = "\n".join(collected_lines).strip()
            self._message_count += 1
            self._status = "done"

            if process.returncode != 0:
                stderr_bytes = (await process.stderr.read()) if process.stderr else b""
                error = stderr_bytes.decode().strip()[:200]
                log.error(f"claude -p error: {error}")
                self._status = "error"
                return f"Hit a problem, sir: {error}"

            log.info(f"Claude Code response for {self._project_name} ({len(response)} chars)")

            # Emit the full response as a `result.markdown` panel event so the
            # panel always has the canonical text — cards (if extracted) render
            # above it as a richer UI layer. Belt-and-suspenders: markdown is
            # the source of truth, cards are opportunistic.
            if task_id and response and len(response) > 150:
                try:
                    await emit_tool_event(
                        task_id,
                        "result.markdown",
                        "Claude response",
                        detail=response[:300],
                        payload={"markdown": response},
                    )
                except Exception:
                    pass

            # Post-completion middleware: extract structured result cards
            # via Haiku. Fire-and-forget — never blocks the return path.
            if task_id and anthropic_client and response:
                try:
                    import claude_middleware
                    asyncio.create_task(
                        claude_middleware.extract_and_emit(
                            response_text=response,
                            tool_result_snippets=tool_result_snippets,
                            task_id=task_id,
                            anthropic_client=anthropic_client,
                        )
                    )
                except Exception as e:
                    log.warning(f"middleware spawn failed: {e}")

            return response

        except Exception as e:
            log.error(f"Work mode error: {e}")
            self._status = "error"
            return f"Something went wrong, sir: {str(e)[:100]}"

    async def stop(self):
        """End the work session."""
        project = self._project_name
        self._active = False
        self._working_dir = None
        self._project_name = None
        self._message_count = 0
        self._status = "idle"
        log.info(f"Work mode ended for {project}")

    def _save_session(self):
        """Persist session state so it survives restarts."""
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps({
                "project_name": self._project_name,
                "working_dir": self._working_dir,
                "message_count": self._message_count,
            }))
        except Exception as e:
            log.debug(f"Failed to save session: {e}")

    def _clear_session(self):
        """Remove persisted session."""
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    async def restore(self) -> bool:
        """Restore session from disk after restart. Returns True if restored."""
        try:
            if SESSION_FILE.exists():
                data = json.loads(SESSION_FILE.read_text())
                self._working_dir = data["working_dir"]
                self._project_name = data["project_name"]
                self._message_count = data.get("message_count", 1)  # Assume at least 1 so --continue is used
                self._active = True
                self._status = "idle"
                log.info(f"Restored work session: {self._project_name} ({self._working_dir})")
                return True
        except Exception as e:
            log.debug(f"No session to restore: {e}")
        return False


def is_casual_question(text: str) -> bool:
    """Detect if a message is casual chat vs work-related.

    Casual questions go to Haiku (fast). Work goes to claude -p (powerful).
    """
    t = text.lower().strip()

    casual_patterns = [
        "what time", "what's the time", "what day",
        "what's the weather", "weather",
        "how are you", "are you there", "hey valet",
        "good morning", "good evening", "good night",
        "thank you", "thanks", "never mind", "nevermind",
        "stop", "cancel", "quit work mode", "exit work mode",
        "go back to chat", "regular mode",
        "how's it going", "what's up",
        "are you still there", "you there", "valet",
        "are you doing it", "is it working", "what happened",
        "did you hear me", "hello", "hey",
        "how's that coming", "hows that coming",
        "any update", "status update",
    ]

    # Short greetings/acknowledgments
    if len(t.split()) <= 3 and any(w in t for w in ["ok", "okay", "sure", "yes", "no", "yeah", "nah", "cool"]):
        return True

    return any(p in t for p in casual_patterns)
