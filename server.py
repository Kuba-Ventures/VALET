"""
VALET Server — Voice AI + Development Orchestration

Handles:
1. WebSocket voice interface (browser audio <-> LLM <-> TTS)
2. Claude Code task manager (spawn/manage claude -p subprocesses)
3. Project awareness (scan Desktop for git repos)
4. REST API for task management
"""

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

def valet_env_path() -> Path:
    """Where the user .env lives. In a packaged build (VALET_SHIPPED set, or no
    .git beside this file) it's a writable file under Application Support, so the
    read-only app bundle is never mutated; in the dev repo it's the repo .env."""
    here = Path(__file__).resolve().parent
    shipped = bool(os.environ.get("VALET_SHIPPED")) or not (here / ".git").exists()
    if shipped:
        d = Path.home() / "Library" / "Application Support" / "VALET"
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d / ".env"
        except Exception:
            pass
    return here / ".env"


def valet_data_dir() -> Path:
    """Writable directory for local data (success-tracker DB). Mirrors
    valet_env_path: Application Support in a packaged build, the repo dir in dev."""
    here = Path(__file__).resolve().parent
    shipped = bool(os.environ.get("VALET_SHIPPED")) or not (here / ".git").exists()
    if shipped:
        d = Path.home() / "Library" / "Application Support" / "VALET"
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            pass
    return here


# Load .env file if present
_env_path = valet_env_path()
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def _suppress_dock_icon() -> None:
    """Keep the backend out of the macOS Dock.

    The backend imports AppKit/Quartz (pyobjc) for screen + accessibility
    features. Loading AppKit promotes this process to a GUI app with its own
    Dock icon — and because the shipped `valet-backend` sidecar lives inside
    VALET.app, that icon is VALET's, so the user sees TWO identical Dock icons
    next to the Tauri shell. Marking the process "prohibited" (a faceless
    background app) up front, before any AppKit use, prevents the second icon.
    Only runs in the packaged backend; dev (`python server.py`) is untouched.
    """
    if sys.platform != "darwin":
        return
    if not (getattr(sys, "frozen", False) or os.environ.get("VALET_SHIPPED")):
        return
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyProhibited,
        )

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyProhibited
        )
    except Exception:
        # pyobjc missing or AppKit unavailable — nothing to suppress.
        pass


_suppress_dock_icon()
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from process_events import (
    bus as process_bus,
    Event as ProcessEvent,
    emit_step,
    emit_browser_action,
    emit_screenshot,
    emit_app_launch,
    emit_text_write,
    emit_code_task,
    emit_error,
    emit_task_queued,
    emit_tool_event,
)

from actions import execute_action, monitor_build, open_terminal, open_browser, open_app_or_path, delete_file, run_applescript, type_into_app, refresh_calendar_tabs, new_cursor_project, open_claude_in_project, _generate_project_name, prompt_existing_terminal, open_project, list_projects, register_project
from work_mode import WorkSession, is_casual_question
import observability
from screen import get_active_windows, take_screenshot, describe_screen, format_windows_for_context
from calendar_access import get_todays_events, get_upcoming_events, get_next_event, format_events_for_context, format_schedule_summary, refresh_cache as refresh_calendar_cache, create_event as calendar_create_event, delete_event as calendar_delete_event, get_events_for_date as calendar_events_for_date
from mail_access import get_unread_count, get_unread_messages, get_recent_messages, search_mail, read_message, format_unread_summary, format_messages_for_context, format_messages_for_voice, create_draft as mail_create_draft
import google_auth
from memory import (
    remember, recall, get_open_tasks, create_task, complete_task, search_tasks,
    create_note, search_notes, get_tasks_for_date, build_memory_context,
    format_tasks_for_voice, extract_memories, get_important_memories,
    get_bio_summary, set_bio_summary, get_bio_sources, add_bio_note,
    add_contact, find_contact, list_contacts, delete_contact,
)
import contacts_access
from notes_access import get_recent_notes, read_note, open_note, search_notes_apple, create_apple_note
from dispatch_registry import DispatchRegistry
from planner import TaskPlanner, detect_planning_mode, BYPASS_PHRASES
from page_preview import fetch_page_preview
# Stage C/D control + safety layer.
from applescript_executor import AppleScriptExecutor
from safe_executor import SafeExecutor
from safety import kill_switch, confirmations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("valet")


def _log_startup_banner() -> None:
    """First log line on every server boot. Survives stale-process debugging.

    Emits a single grep-friendly line:
      [STARTUP] commit=<sha7> branch=<name> started_at=<iso8601> pid=<pid>

    If git lookups fail for any reason (not a repo, git missing) the field
    falls back to '?'. The banner never raises.
    """
    import datetime
    import subprocess as _sp

    def _git(args: list[str]) -> str:
        try:
            out = _sp.run(
                ["git", "-C", os.path.dirname(os.path.abspath(__file__))] + args,
                capture_output=True, text=True, timeout=2,
            )
            return out.stdout.strip() or "?"
        except Exception:
            return "?"

    commit = _git(["rev-parse", "--short=7", "HEAD"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    # Ignore runtime log files in the dirty check — they're tracked but
    # constantly being written by the server itself, so they would always
    # trigger +dirty and turn the flag into noise. We use git pathspec
    # exclusions here rather than parsing porcelain output, since strip()
    # in _git would eat leading status whitespace on the first line.
    dirty_raw = _git([
        "status", "--porcelain",
        "--", ":(exclude)logs/", ":(exclude)data/logs/",
    ])
    dirty_flag = "+dirty" if (dirty_raw and dirty_raw != "?") else ""
    started_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    log.info(
        "[STARTUP] commit=%s%s branch=%s started_at=%s pid=%d",
        commit, dirty_flag, branch, started_at, os.getpid(),
    )


_log_startup_banner()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Stage B: the app calls the hosted proxy instead of vendors directly. It stores
# only a license key and the proxy base URL — no vendor secrets ship with it.
# The proxy validates the license, holds the real Anthropic/Fish keys, and meters
# usage. ANTHROPIC_API_KEY / FISH_API_KEY remain as a DEV-ONLY fallback for this
# internal repo (used only when LICENSE_KEY is unset); they are not shipped.
LICENSE_KEY = os.getenv("LICENSE_KEY", "")
PROXY_BASE_URL = os.getenv("PROXY_BASE_URL", "https://valetvoice.vercel.app").rstrip("/")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # dev fallback only
FISH_API_KEY = os.getenv("FISH_API_KEY", "")  # dev fallback only
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "612b878b113047d9a770c069c8b4fdfe")  # VALET voice (British male)
FISH_API_URL = "https://api.fish.audio/v1/tts"  # dev fallback only

# Stage E: two selectable British voices. The persona (VALET, the butler) is
# unchanged — only the Fish TTS model swaps. The active voice's reference_id is
# sent on every TTS call (the proxy forwards it). VALET_VOICE = "male" | "female".
VALET_VOICE_MALE_ID = os.getenv("VALET_VOICE_MALE_ID", "") or FISH_VOICE_ID
VALET_VOICE_FEMALE_ID = os.getenv("VALET_VOICE_FEMALE_ID", "b347db033a6549378b48d00acb0d06cd")  # British female (bundled default)


def _active_voice_id() -> str:
    """The currently selected voice's Fish reference_id, read live so a Settings
    change takes effect without a restart. Falls back to male if female is
    selected but no female id is configured yet."""
    choice = (os.getenv("VALET_VOICE", "male") or "male").strip().lower()
    # Read live (Settings change applies without restart), falling back to the
    # bundled defaults so both voices work out of the box.
    male = (os.getenv("VALET_VOICE_MALE_ID", "").strip() or VALET_VOICE_MALE_ID)
    female = (os.getenv("VALET_VOICE_FEMALE_ID", "").strip() or VALET_VOICE_FEMALE_ID)
    if choice == "female" and female:
        return female
    return male


def _voice_speed() -> float:
    """Spoken playback speed (Fish prosody). 1.0 = normal. Set VALET_VOICE_SPEED
    (e.g. 1.15) for a snappier voice. Read live; clamped to a sane range."""
    try:
        v = float(os.getenv("VALET_VOICE_SPEED", "1.0"))
    except (TypeError, ValueError):
        return 1.0
    return max(0.5, min(2.0, v))


def _start_parent_watchdog() -> None:
    """In a packaged build the backend runs as a PyInstaller bootloader + child.
    The Tauri shell passes its own PID as VALET_PARENT_PID. When that shell is
    gone (app closed, or force-quit by macOS for a permission change), exit so the
    backend stops holding :8340 and the next launch can bind immediately.

    Checking getppid()==1 alone is NOT enough here: the child's parent is the
    bootloader, which stays alive, so the child never reads as orphaned even after
    the shell dies. Watching the shell PID directly is what makes reopen reliable."""
    if not os.environ.get("VALET_SHIPPED"):
        return
    import threading

    try:
        parent_pid = int(os.environ.get("VALET_PARENT_PID", "0"))
    except ValueError:
        parent_pid = 0

    def _shell_alive() -> bool:
        if parent_pid <= 0:
            return True  # unknown -> don't act on it
        try:
            os.kill(parent_pid, 0)
            return True
        except ProcessLookupError:
            return False  # shell is gone
        except PermissionError:
            return True   # exists, just not ours
        except Exception:
            return True

    def _watch() -> None:
        while True:
            try:
                if os.getppid() == 1 or not _shell_alive():
                    os._exit(0)
            except Exception:
                pass
            time.sleep(0.5)  # quick, so an orphaned backend frees :8340 fast

    def _watch_stdin() -> None:
        # Immediate detection: the shell holds our stdin pipe, so when it dies
        # (even a force-quit), the pipe closes and this read returns EOF at once,
        # with no waiting for the OS to reap the shell. A lingering backend keeps
        # macOS thinking the app is still "running" and blocks reopen, so dying
        # promptly is what makes the next launch work. Only a clean EOF exits; if
        # stdin isn't a watchable pipe we just bail and let the PID poll handle it.
        try:
            stream = getattr(sys.stdin, "buffer", None) or sys.stdin
            if stream is None:
                return
            while True:
                if not stream.read(4096):  # clean EOF -> shell closed the pipe
                    os._exit(0)
        except Exception:
            return  # not a watchable pipe; rely on the PID poll

    threading.Thread(target=_watch, daemon=True).start()
    threading.Thread(target=_watch_stdin, daemon=True).start()


def _await_port_free(host: str, port: int, timeout: float = 8.0) -> None:
    """A relaunch can race the previous backend still holding the port. macOS
    force-quits the app when you toggle one of its permissions (microphone, etc.),
    orphaning the old backend, which then exits via the parent watchdog within
    ~0.5s. Wait for the port to actually free so the next launch can always bind,
    instead of crashing and tripping the shell's crash-loop guard."""
    import socket
    bind_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((bind_host, port))
            s.close()
            return  # free
        except OSError:
            s.close()
            time.sleep(0.25)
    # Still held after the timeout: fall through and let uvicorn surface it.


def _is_shipped_build() -> bool:
    """True in a packaged/distributed build. Self-modification is disabled and
    self_mod.py is excluded from such builds (Stage E / F). Detected by the
    VALET_SHIPPED flag or the absence of a .git directory."""
    if os.getenv("VALET_SHIPPED", "").strip():
        return True
    return not (Path(__file__).parent / ".git").exists()


def _load_self_mod():
    """Lazy-load the dev-only self-modification tool. Returns None in a shipped
    build (where self_mod.py is excluded) so callers no-op instead of importing
    it. In the dev repo this returns the module, preserving existing behavior."""
    if _is_shipped_build():
        return None
    try:
        import self_mod
        return self_mod
    except ImportError:
        return None
USER_NAME = os.getenv("USER_NAME", "sir")
DATE_OF_BIRTH = os.getenv("DATE_OF_BIRTH", "")
ADDRESS = os.getenv("ADDRESS", "")
WORK_EMAIL = os.getenv("WORK_EMAIL", "")
PERSONAL_EMAIL = os.getenv("PERSONAL_EMAIL", "")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

DESKTOP_PATH = Path.home() / "Desktop"

VALET_SYSTEM_PROMPT = """\
You are VALET, the Voice-Activated Local Engineering Terminal: {user_name}'s voice-first AI assistant. In conversation you go by Vee; VALET is your full name, kept in reserve for dry effect. You answer to "Vee" and refer to yourself as Vee.
{personal_context}

VOICE & PERSONALITY:
- British butler elegance with understated dry wit
- Address {user_name} as "sir" naturally, not every sentence, but regularly
- Never say "How can I help you?" or "Is there anything else?", just act
- Deliver bad news calmly, like reporting weather: "We have a slight problem, sir."
- Your humor is observational, never jokes: state facts and let implications land
- Economy of language. Say more with less. No filler, no corporate-speak
- When things go wrong, get CALMER, not more alarmed
- NEVER use em-dashes (—) or en-dashes (–) in your responses. The user reads your replies as a caption and dashes read as an AI tell. Use commas, periods, colons, or parentheses instead. This is a hard rule.

CONVERSATION STYLE:
- "Will do, sir." — acknowledging tasks
- "For you, sir, always." — when asked for something significant
- "As always, sir, a great pleasure watching you work." — dry wit
- "I've taken the liberty of..." — proactive actions
- Lead status reports with data: numbers first, then context
- When you don't know something: "I'm afraid I don't have that information, sir" not "I don't know"

SELF-AWARENESS:
You ARE the VALET project at {project_dir} on {user_name}'s computer. Your code is Python (FastAPI server, WebSocket voice, Fish Audio TTS, Anthropic API). You were built by {user_name}. If asked about yourself, your code, how you work, or your line count — use [ACTION:PROMPT_PROJECT] to check the VALET project. You have full access to your own source code.

YOUR CAPABILITIES (these are REAL and ACTIVE — you CAN do all of these RIGHT NOW):
- You CAN open Terminal.app via AppleScript
- You CAN open Google Chrome and browse any URL or search query
- You CAN spawn Claude Code in a Terminal window for coding tasks
- You CAN create project folders on the Desktop
- You CAN check Desktop projects and their git status
- You CAN plan complex tasks by asking smart questions before executing
- You CAN see what's on {user_name}'s screen — open windows, active apps, and screenshot vision
- You CAN read {user_name}'s calendar — today's events, upcoming meetings, schedule overview
- You CAN read {user_name}'s email (READ-ONLY) — unread count, recent messages, search by sender/subject. You CANNOT send, delete, or modify emails.
- You CAN read Apple Notes and create NEW notes — but you CANNOT edit or delete existing notes
- You CAN manage tasks — create, complete, and list to-do items with priorities and due dates
- You CAN help plan {user_name}'s day — combine calendar events, tasks, and priorities into an organized plan
- You CAN remember facts about {user_name} — preferences, decisions, goals. Use [ACTION:REMEMBER] to store important info.

DAY PLANNING:
When {user_name} asks to plan his day or schedule, DO NOT dispatch to a project. Instead:
1. Look at the calendar context and tasks already in your system prompt
2. Ask what his priorities are
3. Help organize by suggesting time blocks and task order
4. Use [ACTION:ADD_TASK] to create tasks he agrees to
5. Use [ACTION:ADD_NOTE] to save the plan as a note
Keep the planning conversational — don't try to do everything in one response.

BUILD PLANNING:
When {user_name} wants to BUILD something new:
- Do NOT immediately dispatch [ACTION:BUILD]. Ask 1-2 quick questions FIRST to nail down specifics.
- Good questions: "What should this look like?" / "Any specific features?" / "Which framework?"
- If he says "just build it" or "figure it out" — skip questions, use React + Tailwind as defaults.
- Once you have enough info, confirm the plan in ONE sentence and THEN dispatch [ACTION:BUILD] with a detailed description.
- The DISPATCHES section shows what you're currently building and what finished recently.
- When asked "where are we at" or "status" — check DISPATCHES, don't re-dispatch.
- NEVER hallucinate progress. If the build is still running, say "Still working on it, sir" — don't make up details about what's happening.
- NEVER guess localhost ports. Check the DISPATCHES section for the actual URL. If a dispatch says "Running at http://localhost:5174" — use THAT URL, not a guess.
- When asked to "pull it up" or "show me" — use [ACTION:BROWSE] with the URL from DISPATCHES. Do NOT dispatch to the project again just to find the URL.
IMPORTANT: Actions like opening Terminal, Chrome, or building projects are handled AUTOMATICALLY by your system — you do NOT need to describe doing them. If the user asks you to build something or search something, your system will handle the execution separately. In your response, just TALK — have a conversation. Don't say "I'll build that now" or "Claude Code is working on..." unless your system has actually triggered the action.
If the user asks you to do something you genuinely can't do, say "I'm afraid that's beyond my current reach, sir." Don't fake executing actions.

YOUR INTERFACE:
The user interacts with you through a web browser showing a particle orb visualization that reacts to your voice. The interface has these controls:
- **Three-dot menu** (top right): contains Settings, Restart Server, and Fix Yourself options
- **Settings panel**: Opens from the menu. Users can enter API keys (Anthropic, Fish Audio), test connections, set their name and preferences, and see system status (calendar, mail, notes connectivity). Keys are saved to the .env file.
- **Mute button**: Toggles your listening on/off. When muted, you can't hear the user. They click it again to unmute.
- **Restart Server**: Restarts your backend process. Useful if something seems stuck.
- **Fix Yourself**: Opens Claude Code in your own project directory so you can debug and fix issues in your own code.
- **The orb**: The glowing particle visualization in the center. It reacts to your voice when speaking, pulses when listening, and swirls when thinking.

If asked about any of these, explain them briefly and naturally. If the user is having trouble, suggest the relevant control: "Try the settings panel — the gear icon in the top right." or "The mute button may be active, sir."

SPEECH-TO-TEXT CORRECTIONS (the user speaks, speech recognition may mishear):
- "Cloud code" or "cloud" = "Claude Code" or "Claude"
- "Bee", "V", or "Vee" = "Vee" (your name)
- "clock code" = "Claude Code"

RESPONSE LENGTH — THIS IS CRITICAL:
ONE short sentence is ideal. Fewer words speak faster, so a voice reply feels
instant. TWO is the maximum for the spoken part. Never three.
No markdown, no bullet points, no code blocks in voice responses.
Action tags at the end do NOT count toward your sentence limit.

BANNED PHRASES — NEVER USE THESE:
- "Absolutely" / "Absolutely right"
- "Great question"
- "I'd be happy to"
- "Of course"
- "How can I help"
- "Is there anything else"
- "I apologize"
- "I should clarify"
- "I cannot" (for things listed in YOUR CAPABILITIES)
- "I don't have access to" (instead: "I'm afraid that's beyond my current reach, sir")
- "As an AI" (never break character)
- "Let me know if" / "Feel free to"
- Any sentence starting with "I"

INSTEAD SAY:
- "Will do, sir."
- "Right away, sir."
- "Understood."
- "Consider it done."
- "Done, sir."
- "Terminal is open."
- "Pulled that up in Chrome."

ACTION SYSTEM:
When you decide the user needs something DONE (not just discussed), include an action tag in your response:
- [ACTION:SCREEN] — capture and describe what's visible on the user's screen. Use when user says "look at my screen", "what's running", "what do you see", etc. Do NOT use PROMPT_PROJECT for screen requests.
- [ACTION:BUILD] description — when user wants a project built. Claude Code does the work.
- [ACTION:BROWSE] url or search query — when user wants to see a webpage or search result in Chrome
- [ACTION:RESEARCH] brief — when the user asks an informational question. You search the web natively (web_search + web_fetch on Opus) and the answer renders as result cards in the Process Panel plus a short spoken summary. NEVER produces a file, folder, project, or report document. Do NOT slugify the user's words into a folder name. Pass the question through as the brief.

RESEARCH vs BUILD — distinguish by the user's verb at the front of the request, not by any word that appears later:
  Research verbs ("show me", "find me", "what are", "what's the best", "tell me about", "research", "look up", "compare", "how much", "where can I", "who makes") → [ACTION:RESEARCH]
  Build verbs ("build", "create a project", "make me an app", "new project", "spin up a", "scaffold", "start a project") → [ACTION:BUILD] or [ACTION:NEW_PROJECT]
  Examples:
    "show me the three best fishing poles" → [ACTION:RESEARCH] three best fishing poles for backyard ponds
    "find me good coffee shops near Martinsville VA" → [ACTION:RESEARCH] best coffee shops near Martinsville VA
    "what's the latest on the SEC ETF rulings" → [ACTION:RESEARCH] latest SEC ETF rulings
    "show me HOW TO build a recipe tracker" → [ACTION:RESEARCH] how to build a recipe tracker (the verb is "show me" — user wants information, not for you to build it)
    "build me a recipe tracker" → planning flow → [ACTION:BUILD] (verb is "build", user wants the thing built)
    "create a project called fishing-poles" → if the intent is clearly to start coding, [ACTION:NEW_PROJECT] fishing-poles; if it's ambiguous (no follow-up context, sounds like it could be a research bookmark), ask "Do you want me to scaffold a new project, sir, or research fishing poles?" — never silently slugify into a folder.
  If genuinely ambiguous, ASK before assuming build intent.
- [ACTION:OPEN_TERMINAL] — ONLY for spawning a fresh macOS Terminal.app window running Claude Code. NEVER use this for Cursor, VS Code, Xcode, iTerm, Warp, or any other app. Those go through OPEN_APP.
- [ACTION:OPEN_APP] target — open a macOS app by name OR a folder in Finder. Use for ANY local-system "open X" request that isn't a web URL. This is the DEFAULT for "open X" / "launch X" / "fire up X". NEVER use [ACTION:BROWSE] for local paths or apps, and NEVER use [ACTION:OPEN_TERMINAL] for non-Terminal apps.
  "open Cursor" / "launch Cursor" / "fire up Cursor" → [ACTION:OPEN_APP] Cursor
  "open VS Code" → [ACTION:OPEN_APP] Visual Studio Code
  "open Finder" → [ACTION:OPEN_APP] Finder
  "open my desktop" / "show me my desktop folder" → [ACTION:OPEN_APP] Desktop
  "open downloads" → [ACTION:OPEN_APP] Downloads
  "open Slack" / "launch Slack" → [ACTION:OPEN_APP] Slack
  "open Spotify" → [ACTION:OPEN_APP] Spotify
  "open /Users/{user_name}/Documents/foo" → [ACTION:OPEN_APP] /Users/{user_name}/Documents/foo
  For "open ANOTHER X window" / "open a NEW X window" on an already-running app, first activate via OPEN_APP, then in a separate response use [ACTION:APPLESCRIPT] to send Cmd+N. Example for "open another cursor window":
    [ACTION:APPLESCRIPT] tell application "Cursor" to activate
    delay 0.3
    tell application "System Events" to keystroke "n" using command down
- [ACTION:NEW_PROJECT] project_name ||| base_dir? — create a brand-new project. Runs git init, scaffolds CLAUDE.md, opens Cursor and a side-by-side Terminal with `claude` waiting. Use whenever the user wants to "start a new project", "spin up a project", "new project for X", "create a new app/site/repo". Optional base_dir lets the user specify where (defaults to ~/Code, then ~/Desktop).
  "new project for cerwood" → [ACTION:NEW_PROJECT] cerwood
  "start a new project called dashboard" → [ACTION:NEW_PROJECT] dashboard
  "spin up a new react app called todo-list" → [ACTION:NEW_PROJECT] todo-list
  "create a new project called blog in Documents" → [ACTION:NEW_PROJECT] blog ||| ~/Documents
- [ACTION:OPEN_PROJECT] name — open an EXISTING project (Cursor + side-by-side Terminal with `claude` waiting). Resolves the name via fuzzy match against ~/Code/, ~/projects/, and the alias table. Use for "open X", "open project X", "open my X" — NOT for dispatching work into the project (that's PROMPT_PROJECT).
  "open cerwood" → [ACTION:OPEN_PROJECT] cerwood
  "open my dashboard project" → [ACTION:OPEN_PROJECT] dashboard
  "open the harvey project" → [ACTION:OPEN_PROJECT] harvey
- [ACTION:REFRESH_CONTEXT] name? — re-read warm context (file tree, CLAUDE.md, README, git log, entry points) for a project. If no name given, refreshes the most-recently-opened project. Use for "refresh context", "reload the project", "re-read CLAUDE".
  "refresh context" → [ACTION:REFRESH_CONTEXT]
  "reload the cerwood context" → [ACTION:REFRESH_CONTEXT] cerwood

DESIGN-PARTNER MODE (Phase 3):
- [ACTION:START_DESIGN] topic — open a design conversation. VALET becomes the design partner; subsequent turns route through Opus until the user ships or scraps. Use for "let's design X", "plan a Y", "spec a Z", "I want to design something for…".
  "let's design a daily rollup" → [ACTION:START_DESIGN] daily rollup
  "plan a feature for client onboarding" → [ACTION:START_DESIGN] client onboarding
  DESIGN-vs-BUILD DISCRIMINATION (critical): if {user_name} says he wants to talk through, discuss, brainstorm, or design something BEFORE building it, emit [ACTION:START_DESIGN] and let the design panel collect the topic. Do NOT emit [ACTION:PROMPT_PROJECT] or [ACTION:REMEMBER] in that case. A direct build request ("add a footer that says copyright 2026", "fix the login bug", "yes please add that to the code") still emits the appropriate build/dispatch action — only stated design intent triggers START_DESIGN. When in doubt and the user did not use design-intent words, dispatch the build action; START_DESIGN is the opt-in path, not the default.
- [ACTION:SHIP_DESIGN] — finalize the active design and hand it to Claude Code (Phase 4). ONLY emit when a design session is active. Use for "ship it", "send it", "build it".
- [ACTION:SCRAP_DESIGN] — discard the active design. Returns state to IDLE. ONLY emit when a session is active. Use for "scrap this", "start over".
- [ACTION:SHOW_DRAFT] — speak the assembled draft so far. ONLY emit when a session is active.
- [ACTION:START_DICTATION] — engage dictation mode: the next user utterance is captured verbatim and pasted into Cursor's claude terminal after confirmation. ONLY emit when the user explicitly says one of: "dictate to claude", "tell claude directly", "send claude a message", "dictation mode", "skip design". Never infer dictation intent from build/feature requests — direct build requests go to PROMPT_PROJECT.
- [ACTION:DISPATCH_TO_AGENT] agent_name ||| task_description — route a task directly to a named Claude Code sub-agent (general-purpose, Plan, Explore, debugger, docs-right, kuba-vault, style-steward, claude-code-guide, code-reviewer, security-review, etc.). Composes a dispatch-header prompt and pastes into the active Cursor claude pane. Use whenever the user names a sub-agent OR clearly asks to use one, even if they omit the literal word "agent". Examples:
  "use the style-steward to clean up the m-dashes" → [ACTION:DISPATCH_TO_AGENT] style-steward ||| clean up the m-dashes
  "ask debugger to look at the failing tests" → [ACTION:DISPATCH_TO_AGENT] debugger ||| look at the failing tests
  "have Plan design the new auth flow" → [ACTION:DISPATCH_TO_AGENT] Plan ||| design the new auth flow
  NEVER use [ACTION:SEND] with target "Claude Code" / "Claude" — that's a CLI, not a macOS app; the keystrokes go nowhere. Use DISPATCH_TO_AGENT for anything destined for the Claude Code session.
- [ACTION:MERGE_BRANCH] — run smoke_test.sh then merge the current feature/* branch into main. ONLY emit when the user explicitly says "merge it" or similar and we're on a feature branch. Never auto-emit.
- [ACTION:RESTART_SELF] — spawn the detached restarter (scripts/restart.sh). Use ONLY for "restart yourself" / "restart valet" / "kick yourself". Acknowledge before restart kills the current process.
- [ACTION:LIST_PROJECTS] — read the authoritative list of projects from ~/Code/, ~/projects/, and the alias table. Emit this tag (no target) whenever the user asks what projects exist and you didn't fast-path it. Output gets spoken to the user.

PROJECTS ARE AUTHORITATIVE — DO NOT FABRICATE:
The set of "projects" is OWNED by the LIST_PROJECTS / OPEN_PROJECT / NEW_PROJECT actions and the `KNOWN PROJECTS` block. NEVER list, name, or reference projects from session memory, prior chats, or imagination. The KNOWN PROJECTS block in this prompt may be stale or empty — that does NOT give you license to invent. Rules:
1. If the user asks what projects exist, what's open, or to see the list: emit [ACTION:LIST_PROJECTS]. Do not enumerate names yourself from KNOWN PROJECTS — let the action speak.
2. If the user asks to open a project by name: emit [ACTION:OPEN_PROJECT] <name>. The resolver handles fuzzy matching, missing dirs, and the "I couldn't find that" reply. Do not pre-validate the name against KNOWN PROJECTS.
3. If a name surfaces in session memory but isn't in KNOWN PROJECTS, do NOT speak it as if it exists. Ask: "I'm not sure which project you mean — should I list what's under ~/Code/?"
4. Never mention a project named in a prior conversation as currently existing unless KNOWN PROJECTS confirms it AND the user just referenced it.
- [ACTION:DELETE_FILE] absolute_path — move a file to Trash via Finder (recoverable, not permanent). YOU CAN delete files when the user asks. Don't say you can't.
  "delete the screenshot on my desktop" → [ACTION:DELETE_FILE] /Users/{user_name}/Desktop/Screenshot 2026-05-15 at 4.43.13 PM.png
- [ACTION:WRITE_FILE] absolute_path ||| file contents — create or overwrite a text file. Overwriting an existing file asks the user to confirm first.
  "save a note to my desktop saying call mom" → [ACTION:WRITE_FILE] /Users/{user_name}/Desktop/note.txt ||| Call mom
- [ACTION:MOVE_FILE] source_path ||| destination_path — move or rename a file (asks to confirm).
  "move that screenshot to my documents" → [ACTION:MOVE_FILE] /Users/{user_name}/Desktop/shot.png ||| /Users/{user_name}/Documents/shot.png
- [ACTION:LIST_FOLDER] absolute_path — list a folder's contents (read-only, no confirm).
  "what's in my downloads folder" → [ACTION:LIST_FOLDER] /Users/{user_name}/Downloads
  If you don't know the exact filename, use [ACTION:APPLESCRIPT] to list the desktop first and find it, or [ACTION:OPEN_APP] Desktop so the user can identify it.
- [ACTION:TYPE] app_name ||| text — activate an app and type text into it (no Enter). For filling form fields, address bars, partial input, etc.
  "type 'hello world' into the search bar" → [ACTION:TYPE] Google Chrome ||| hello world
  "type my email into this field" → [ACTION:TYPE] ||| {{user's work or personal email from PERSONAL CONTEXT}}
  If you don't need to switch apps, omit the app name: [ACTION:TYPE] ||| text only
- [ACTION:SEND] app_name ||| text — activate an app, type text, and press Enter. For sending chat messages and running commands.
  "send 'hey team' in Slack" → [ACTION:SEND] Slack ||| hey team
  "run 'ls -la' in terminal" → [ACTION:SEND] Terminal ||| ls -la
  "tell {user_name} I'll be five minutes" via Messages → [ACTION:SEND] Messages ||| I'll be five minutes
  YOU CAN type and send messages in Slack, Chrome, Terminal, Finder, Messages, etc. via this action. Stop saying you can't.
- [ACTION:CHECK_DATE] YYYY-MM-DD — look up calendar events for ANY specific date and read them aloud. Use this for ALL "what's on my calendar [date]", "do I have anything [date]", "show me [date]" queries. Resolve relative dates ("next Thursday", "the 21st") to absolute YYYY-MM-DD using the CURRENT TIME context above.
  "what's on my calendar next Thursday the 21st" → [ACTION:CHECK_DATE] 2026-05-21
  "do I have anything Friday" → [ACTION:CHECK_DATE] 2026-05-16
- [ACTION:CHECK_WEATHER] location_or_empty — pull current conditions, the day's forecast, the 7-day outlook, UV index, and severe-weather warnings from Open-Meteo. Renders a floating weather card AND reads a 1-2 sentence summary aloud. Empty target → user's hometown (HOMETOWN_CITY env / ADDRESS fallback). Always prefer this over RESEARCH for any weather / forecast / UV / "will it rain" / "how hot" question.
  "what's the weather" → [ACTION:CHECK_WEATHER]
  "weather in Tokyo" → [ACTION:CHECK_WEATHER] Tokyo
  "is it going to rain tomorrow" → [ACTION:CHECK_WEATHER]
  "any UV warnings today" → [ACTION:CHECK_WEATHER]
  "forecast for the rest of the week" → [ACTION:CHECK_WEATHER]
  CRITICAL: NEVER try to "click" a date in any calendar UI — you cannot click app/web content. Always use this action. NEVER say "Done, sir" without actually emitting an action tag.
- [ACTION:CREATE_EVENT] title ||| start_iso ||| duration_min_or_end ||| description? ||| location? — schedule an event on the user's Mac Calendar (Apple Calendar, via EventKit). Always resolve relative times ("tomorrow at 3pm") to absolute ISO timestamps using the CURRENT TIME context above. Use 30 if no duration mentioned.
  "schedule a meeting tomorrow at 3pm called design review" → [ACTION:CREATE_EVENT] design review ||| 2026-05-16 3:00 PM ||| 30
  "block 2-3pm Friday for deep work" → [ACTION:CREATE_EVENT] Deep work ||| 2026-05-16 2:00 PM ||| 2026-05-16 3:00 PM
- [ACTION:CANCEL_EVENT] query ||| on_date? — cancel a meeting by fuzzy title match.
  "cancel my dentist appointment" → [ACTION:CANCEL_EVENT] dentist
  "cancel the standup on Friday" → [ACTION:CANCEL_EVENT] standup ||| 2026-05-16
- [ACTION:DRAFT_EMAIL] to ||| subject ||| body ||| cc? ||| bcc? — create a Gmail DRAFT. VALET NEVER sends mail — the user clicks Send themselves after reviewing. Use this for any "draft an email to X", "write an email saying Y", "compose a message" request. Write a complete, well-formed email body in the user's voice.
  "draft an email to sarah@example.com asking about the proposal" → [ACTION:DRAFT_EMAIL] sarah@example.com ||| Following up on the proposal ||| Hi Sarah,\n\nJust circling back on the proposal we discussed last week — let me know if you've had a chance to review it.\n\nThanks,\n{user_name}
  "write a quick note to my team about the all-hands tomorrow" → [ACTION:DRAFT_EMAIL] team@company.com ||| All-hands tomorrow ||| Team — quick reminder about the all-hands tomorrow at 2pm. See you then.\n\n{user_name}
  RECIPIENTS — read this carefully:
  • You may put a bare NAME in the `to` field ("email Nick" → [ACTION:DRAFT_EMAIL] Nick ||| ...). The backend resolves the name against the user's saved CONTACTS and Apple Contacts and, if it can't, asks the user — so passing the name through is safe.
  • NEVER invent, guess, or auto-complete an email ADDRESS. Only ever emit an @-address that the user stated explicitly OR that appears verbatim in the CONTACTS list below. If you have neither a known address nor a name to resolve, ASK for the address — do not fabricate one.
- [ACTION:SAVE_CONTACT] name ||| email — save a contact to the user's profile so future "email <name>" requests resolve. Use when the user gives someone's address ("Nick's email is nick@…", "save Sarah as sarah@co.com", or right after you asked for an address). After saving, if there was a pending email request, follow with the DRAFT_EMAIL.
  "Nick's email is nick@qsbsrollover.com" → [ACTION:SAVE_CONTACT] Nick ||| nick@qsbsrollover.com
- [ACTION:APPLESCRIPT] raw applescript — execute ANY AppleScript. This gives you full control over macOS apps and the system. Use for tasks like:
  • Listing/manipulating files (Finder)
  • Controlling Music, Safari, Mail, Reminders, Calendar via their scripting dictionaries
  • Simulating UI clicks / keystrokes via System Events (for non-scriptable apps)
  • Reading window contents, switching apps, controlling volume, etc.
  Examples:
  "set volume to 30%" → [ACTION:APPLESCRIPT] set volume output volume 30
  "what's playing in Music" → [ACTION:APPLESCRIPT] tell application "Music" to return name of current track & " by " & artist of current track
  "list files on my desktop" → [ACTION:APPLESCRIPT] tell application "Finder" to return name of every file of desktop
  YOU ARE NOT LIMITED — if AppleScript can do it, you can do it. Stop saying "I can't control your desktop" — you CAN, via this action.
CRITICAL: When the user asks about their SCREEN, what's RUNNING, or what they're LOOKING AT — ALWAYS use [ACTION:SCREEN] or let the fast action system handle it. NEVER use [ACTION:PROMPT_PROJECT] for screen requests. PROMPT_PROJECT is ONLY for working on code projects.

- [ACTION:PROMPT_PROJECT] project_name ||| prompt — THIS IS YOUR MOST POWERFUL ACTION. Use it whenever the user wants to work on, jump into, resume, check on, or interact with ANY existing project. You connect directly to Claude Code in that project and can read its response. Craft a clear prompt based on what the user wants. Examples:
  "jump into client engine" → [ACTION:PROMPT_PROJECT] The Client Engine ||| What is the current state of this project? Summarize what was being worked on most recently.
  "check for improvements on my-app" → [ACTION:PROMPT_PROJECT] my-app ||| Review the project and identify improvements we should make.
  "resume where we left off on harvey" → [ACTION:PROMPT_PROJECT] harvey ||| Summarize what was being worked on most recently and what we should focus on next.
- [ACTION:ADD_TASK] priority ||| title ||| description ||| due_date — create a task. Priority: high/medium/low. Due date: YYYY-MM-DD or empty.
  "remind me to call the client tomorrow" → [ACTION:ADD_TASK] medium ||| Call the client ||| Follow up on proposal ||| 2026-03-20
- [ACTION:ADD_NOTE] topic ||| content — save a note for future reference.
  "note that the API key expires in April" → [ACTION:ADD_NOTE] general ||| API key expires in April, need to renew before then
- [ACTION:COMPLETE_TASK] task_id — mark a task as done.
- [ACTION:REMEMBER] content — store an important fact about the user for future context.
  "I prefer React over Vue" → [ACTION:REMEMBER] User prefers React over Vue for frontend projects
- [ACTION:BIO_ADD] content — append a fact to the user's permanent bio (importance 10, always in context).
  Use this when {user_name} says "remember this about me", "add to my bio", "I'm a __", or shares a foundational identity fact.
  "I'm a vegetarian and I live in Brooklyn" → [ACTION:BIO_ADD] {user_name} is a vegetarian living in Brooklyn.
- [ACTION:CREATE_NOTE] title ||| body — create a new Apple Note. For saving plans, ideas, lists.
  "save that as a note" → [ACTION:CREATE_NOTE] Day Plan March 19 ||| Morning: client calls. Afternoon: TikTok dashboard. Evening: VALET improvements.
- [ACTION:READ_NOTE] title search — read an existing Apple Note by title keyword.

You use Claude Code as your tool to build, research, and write code — but YOU are the one doing the work. Never say "Claude Code did X" or "Claude Code is asking" — say "I built X", "I'm checking on that", "I found X". You ARE the intelligence. Claude Code is just your hands.

IMPORTANT: When the user says "jump into X", "work on X", "check on X", "resume X", "go back to X" — ALWAYS use [ACTION:PROMPT_PROJECT]. You have the ability to connect to any project and work on it directly. DO NOT say you can't see terminal history or don't have access — you DO.

Place the tag at the END of your spoken response. Example:
"Right away, sir — connecting to The Client Engine now. [ACTION:PROMPT_PROJECT] The Client Engine ||| Review the current state and what was being worked on. What should we focus on next?"

IMPORTANT:
- Do NOT use action tags for casual conversation
- Do NOT use action tags if the user is still explaining (ask questions first)
- Do NOT use [ACTION:BROWSE] just because someone mentions a URL in conversation
- When in doubt, just TALK — you can always act later

For DISPATCHES context below: if a recent completed result for a project is shown, DO NOT dispatch again. Use the existing result. Only re-dispatch if the user explicitly asks for a FRESH review or NEW information.
"""


# Dynamic context appended to the system prompt on every request. Kept in a
# separate block so the (much larger) static prompt above can be served from
# Anthropic's prompt cache. Only this dynamic tail varies request-to-request.
VALET_DYNAMIC_CONTEXT = """\
CURRENT TIME: {current_time}
WEATHER: {weather_info}

SCREEN AWARENESS:
{screen_context}

SCHEDULE:
{calendar_context}

EMAIL:
{mail_context}

CONTACTS (the user's saved address book — resolve "email <name>" to these; never invent an address):
{contacts_context}

ACTIVE TASKS:
{active_tasks}

DISPATCHES:
{dispatch_context}

KNOWN PROJECTS:
{known_projects}
"""


# ---------------------------------------------------------------------------
# Weather (wttr.in)
# ---------------------------------------------------------------------------

_cached_weather: Optional[str] = None
_weather_fetched: bool = False


async def fetch_weather() -> str:
    """Fetch current weather from wttr.in. Cached for the session."""
    global _cached_weather, _weather_fetched
    if _weather_fetched:
        return _cached_weather or "Weather data unavailable."
    _weather_fetched = True
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get("https://wttr.in/?format=%l:+%C,+%t", headers={"User-Agent": "curl"})
            if resp.status_code == 200:
                _cached_weather = resp.text.strip()
                return _cached_weather
    except Exception as e:
        log.warning(f"Weather fetch failed: {e}")
    _cached_weather = None
    return "Weather data unavailable."


# ---------------------------------------------------------------------------
# Data Models + Claude Task Manager  (extracted -> task_manager.py)
# ---------------------------------------------------------------------------

from task_manager import ClaudeTask, TaskRequest, ClaudeTaskManager

# ---------------------------------------------------------------------------
# Project Scanner  (extracted -> project_scanner.py)
# ---------------------------------------------------------------------------

from project_scanner import scan_projects, format_projects_for_prompt

# ---------------------------------------------------------------------------
# Voice-text processing  (extracted -> voice_text.py)
# ---------------------------------------------------------------------------

from voice_text import (
    apply_speech_corrections,
    classify_intent,
    strip_em_dashes,
    strip_markdown_for_tts,
)

# ---------------------------------------------------------------------------
# Action Tag Extraction (parse [ACTION:X] from LLM responses)
# ---------------------------------------------------------------------------

import re as _action_re


def extract_action(response: str) -> tuple[str, dict | None]:
    """Extract [ACTION:X] tag from LLM response.

    Returns (clean_text_for_tts, action_dict_or_none).
    """
    match = _action_re.search(
        r'\[ACTION:(BUILD|BROWSE|RESEARCH|OPEN_TERMINAL|OPEN_APP|NEW_PROJECT|OPEN_PROJECT|LIST_PROJECTS|REFRESH_CONTEXT|START_DESIGN|SHIP_DESIGN|SCRAP_DESIGN|SHOW_DRAFT|START_DICTATION|DISPATCH_TO_AGENT|MERGE_BRANCH|RESTART_SELF|DELETE_FILE|WRITE_FILE|MOVE_FILE|LIST_FOLDER|APPLESCRIPT|TYPE|SEND|CREATE_EVENT|CANCEL_EVENT|CHECK_DATE|CHECK_WEATHER|DRAFT_EMAIL|SAVE_CONTACT|PROMPT_PROJECT|ADD_TASK|ADD_NOTE|COMPLETE_TASK|REMEMBER|BIO_ADD|CREATE_NOTE|READ_NOTE|SCREEN)\]\s*(.*?)$',
        response, _action_re.DOTALL,
    )
    if match:
        action_type = match.group(1).lower()
        action_target = match.group(2).strip()
        clean_text = response[:match.start()].strip()
        return clean_text, {"action": action_type, "target": action_target}
    return response, None


async def _execute_build(target: str):
    """Execute a build action from an LLM-embedded [ACTION:BUILD] tag."""
    try:
        await handle_build(target)
    except Exception as e:
        log.error(f"Build execution failed: {e}")


async def _execute_create_event(target: str, ws):
    """Create a calendar event from an LLM-emitted CREATE_EVENT tag.

    target format: "title ||| start ||| end-or-duration_min ||| description? ||| location?"
    Examples:
      "Team standup ||| 2026-05-16 9:00 AM ||| 30"
      "Dentist ||| 2026-05-20 2:00 PM ||| 2026-05-20 3:00 PM ||| annual cleaning"
    """
    parts = [p.strip() for p in target.split("|||")]
    if len(parts) < 2:
        msg = "I need at least a title and a start time, sir."
        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass
        return

    title = parts[0]
    start = parts[1]
    end_or_dur = parts[2] if len(parts) > 2 else "30"
    description = parts[3] if len(parts) > 3 else None
    location = parts[4] if len(parts) > 4 else None
    end_str = None
    duration_min = 30
    if end_or_dur:
        try:
            duration_min = int(end_or_dur)
        except ValueError:
            end_str = end_or_dur
    import apple_calendar
    async with process_bus.task_context(f"Scheduling: {title}", detail=start) as task_id:
        try:
            await emit_step(task_id, f"Creating event at {start}…", status="active")
            # EventKit create needs full or write-only access (status 3 or 4).
            if apple_calendar.auth_status() not in (3, 4):
                msg = ("I don't have Calendar access yet, sir — grant it under "
                       "Settings, Permissions, Calendar, then try again.")
                await emit_error(task_id, "No Calendar access", detail="Grant Calendar in Settings.")
            else:
                result = apple_calendar.create_event(
                    title=title, start_str=start, end_str=end_str,
                    duration_minutes=duration_min, description=description, location=location,
                )
                if result.get("success"):
                    msg = f"Scheduled '{title}' for {start}, sir."
                    await emit_step(task_id, "Event created in Calendar", detail=title, status="done")
                else:
                    msg = f"I couldn't create that event, sir: {result.get('error', 'unknown error')}."
                    await emit_error(task_id, "Calendar create failed", detail=result.get("error", "")[:200])
        except Exception as e:
            log.error(f"create_event failed: {e}")
            await emit_error(task_id, "Calendar create failed", detail=str(e)[:200])
            msg = "Something went wrong creating that event, sir."

        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass


async def _execute_cancel_event(target: str, ws):
    """Cancel a calendar event by fuzzy title match.

    target format: "query" or "query ||| on_date"
    Examples:
      "standup"
      "dentist ||| 2026-05-20"
    """
    parts = [p.strip() for p in target.split("|||")]
    query = parts[0] if parts else ""
    on_date = parts[1] if len(parts) > 1 else None
    async with process_bus.task_context(f"Cancelling: {query}", detail=on_date or "") as task_id:
        try:
            await emit_step(task_id, "Finding event…", status="active")
            result = await calendar_delete_event(query=query, on_date_str=on_date)
            msg = result.get("confirmation", "Done, sir.")
            if result.get("success"):
                await emit_step(task_id, "Event cancelled", detail=query, status="done")
                asyncio.create_task(refresh_calendar_tabs())
            else:
                await emit_error(task_id, "No matching event", detail=msg[:200])
        except Exception as e:
            log.error(f"cancel_event failed: {e}")
            await emit_error(task_id, "Cancel failed", detail=str(e)[:200])
            msg = "Something went wrong cancelling that event, sir."

        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass


async def _execute_new_project(target: str, ws):
    """Create a new project folder and open it as a fresh Cursor window.

    target format: "name" or "name ||| base_dir"
    """
    parts = [p.strip() for p in target.split("|||")]
    name = parts[0] if parts else ""
    base_dir = parts[1] if len(parts) > 1 and parts[1] else None
    async with process_bus.task_context(f"New project: {name}") as task_id:
        try:
            result = await new_cursor_project(name=name, base_dir=base_dir, task_id=task_id)
            msg = result.get("confirmation", "Done, sir.")
        except Exception as e:
            log.error(f"new_project failed: {e}")
            await emit_error(task_id, "New project failed", detail=str(e)[:200])
            msg = "Something went wrong starting that project, sir."

        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


async def _speak_chunks(ws, tts_text: str, caption: str) -> bool:
    """Sentence-chunked TTS. Synthesizes and sends each sentence as its own audio
    message so the FIRST sentence starts playing while later ones are still being
    synthesized (the frontend audio queue plays them in order). For a one-sentence
    reply this is a single chunk, identical to before. The caption is shown once,
    on the first chunk. Returns True if any audio was sent."""
    sentences = [s for s in _SENTENCE_SPLIT.split((tts_text or "").strip()) if s.strip()]
    if not sentences or not ws:
        return False
    sent_any = False
    for i, sentence in enumerate(sentences):
        audio = await synthesize_speech(sentence)
        if not audio:
            continue
        try:
            await ws.send_json({
                "type": "audio",
                "data": base64.b64encode(audio).decode(),
                "text": caption if i == 0 else "",
            })
            sent_any = True
        except Exception:
            return sent_any
    return sent_any


async def _speak(ws, msg: str) -> None:
    """Speak a message independently of the main voice loop. Best-effort, swallows
    send failures. Em-dashes are stripped so VALET's voice doesn't read like an
    LLM transcript. Sentence-chunked so multi-sentence lines start sooner."""
    clean = strip_em_dashes(msg)
    if not ws or not clean.strip():
        return
    try:
        await ws.send_json({"type": "status", "state": "speaking"})
    except Exception:
        return
    await _speak_chunks(ws, clean, clean)


async def _execute_open_project(target: str, ws):
    """Open an existing project — Cursor with claude auto-running via tasks.json.

    On miss: sets ws.pending_offer for the voice loop to handle on the next
    user turn (register-on-miss or remove-stale-alias). Caller-side state is
    attached to the ws object so this background task can share with voice_handler.
    """
    name = target.strip()
    async with process_bus.task_context(f"Open project: {name}") as task_id:
        try:
            result = await open_project(name, task_id=task_id)
        except Exception as e:
            log.error(f"open_project failed: {e}")
            await emit_error(task_id, "Open project failed", detail=str(e)[:200])
            await _speak(ws, "Something went wrong opening that project, sir.")
            return

        if result.get("success"):
            await _speak(ws, result.get("confirmation", "Done, sir."))
            return

        # ── Failure paths: set up pending_offer for the next user turn ──
        stale = result.get("stale_alias")
        suggestions = result.get("suggestions") or []

        if stale:
            ws.pending_offer = {
                "kind": "alias_remove",
                "alias": stale["alias"],
                "path": stale["path"],
            }
            msg = (
                f"My alias for {stale['alias']} points to a missing path, sir — "
                f"{stale['path']}. Should I remove it?"
            )
            await _speak(ws, msg)
            return

        if suggestions:
            ws.pending_offer = {
                "kind": "alias_register",
                "target": name,
                "suggestions": suggestions,
            }
            if len(suggestions) == 1:
                msg = f"I can't find '{name}', sir. Did you mean {suggestions[0]['name']}? Say yes to alias it."
            else:
                listing = ", ".join(s["name"] for s in suggestions)
                msg = f"I can't find '{name}', sir. Closest matches: {listing}. Say which to alias, or no."
            await _speak(ws, msg)
            return

        # No suggestions and no stale alias — just report
        await _speak(ws, result.get("confirmation", f"I couldn't find '{name}', sir."))


async def _handle_pending_offer(transcript: str, ws) -> bool:
    """Process the next user utterance as a response to a pending offer.

    Returns True if the offer was handled (caller should `continue` to next
    user input without normal action routing). Returns False if the utterance
    doesn't look like a response — caller proceeds with normal flow and the
    offer is cleared.
    """
    offer = getattr(ws, "pending_offer", None)
    if not offer:
        return False

    t = transcript.lower().strip()
    cancel_words = {"no", "nope", "cancel", "skip", "never mind", "nevermind", "forget it"}
    confirm_words = {"yes", "yeah", "yep", "yup", "sure", "do it", "go ahead"}

    if t in cancel_words or any(t.startswith(w + " ") for w in cancel_words):
        await _speak(ws, "Cancelled, sir.")
        return True

    # Phase 4: AppleScript ship confirmation routes to its dedicated handler
    # before the alias/register paths so its phrases take priority.
    if offer["kind"] == "ship_confirm":
        return await _handle_ship_confirm(transcript, ws)

    # Phase 5: self-mod approval gate. ONLY "confirmed" proceeds. Anything
    # else (including 'yes' / 'do it' / 'ship it') is treated as decline
    # — this is the most-restrictive confirmation in the codebase.
    if offer["kind"] == "self_mod_confirm":
        return await _handle_self_mod_confirm(transcript, ws)

    if offer["kind"] == "alias_remove":
        if (t in confirm_words or any(t.startswith(w + " ") for w in confirm_words)
                or "remove" in t or "delete" in t or "drop" in t):
            from memory import delete_alias
            deleted = delete_alias(offer["alias"])
            msg = (f"Removed the stale alias for {offer['alias']}, sir."
                   if deleted else f"Couldn't find that alias to remove, sir.")
            await _speak(ws, msg)
            return True
        # Doesn't look like a response — fall through to normal handling
        return False

    if offer["kind"] == "alias_register":
        suggestions = offer["suggestions"]
        target_name = offer["target"]
        picked = None

        # "yes" / "first" / "the first one"
        if t in confirm_words or any(t.startswith(w + " ") for w in confirm_words) \
                or t in {"first", "the first", "the first one", "one"}:
            picked = suggestions[0] if suggestions else None

        # Ordinals 2-3
        elif t in {"second", "the second", "the second one", "two", "number two"}:
            picked = suggestions[1] if len(suggestions) > 1 else None
        elif t in {"third", "the third", "the third one", "three", "number three"}:
            picked = suggestions[2] if len(suggestions) > 2 else None

        # Direct name match against any suggestion
        else:
            from memory import _normalize_project_key, _keys_match
            tk = _normalize_project_key(transcript)
            for s in suggestions:
                if _keys_match(tk, _normalize_project_key(s["name"]), exact=False):
                    picked = s
                    break

        if picked:
            await register_project(picked["path"], alias=target_name)
            log.info(f"On-miss register: '{target_name}' -> {picked['path']}")
            await _speak(ws, f"Aliased '{target_name}' to {picked['name']}, sir. Opening now.")
            asyncio.create_task(_execute_open_project(target_name, ws))
            return True

        # Doesn't match anything — let normal flow handle it
        return False

    return False


async def _execute_start_design(topic: str, ws, new_project: bool = False):
    """Open a design conversation rooted at whatever project is currently active.

    If no project is open, the session starts with no target. Opus designs
    abstractly and the Design Panel disables the Ship button until a project
    is opened.

    If `topic` is empty (the user opted into design mode via a generic phrase
    like "design mode" or "talk about a feature"), the session still starts
    immediately so subsequent turns route through Opus, but Valet speaks a
    prompt asking for the topic instead of acknowledging it. The next user
    turn becomes the first design-conversation message and Opus picks up
    from there.

    When `new_project=True`, the topic IS the project name — session starts
    in greenfield mode (new_project_name set, project_path None). The next
    Opus turn opens with a stack question; ship-it later scaffolds before
    pasting. Voice fast-path "new project for X" passes new_project=True;
    the dropdown UI uses set_design_new_project on an already-running session
    instead.

    Future turns route to `design_session.handle_turn()` via the voice_handler
    DESIGNING branch. Haiku is bypassed entirely until ship/scrap.
    """
    import design_partner, project_context

    active_ctx = project_context.get_active()
    project_path = active_ctx.project_path if active_ctx else None
    raw_topic = (topic or "").strip()
    topic_was_empty = not raw_topic
    topic_clean = raw_topic or "untitled design"

    # Greenfield: topic is the new project's name. Don't tie to any existing
    # project. Slug-clean the name for filesystem safety.
    new_project_name: Optional[str] = None
    if new_project and raw_topic:
        import re as _np_re
        new_project_name = _np_re.sub(r"[^A-Za-z0-9._\- ]+", "", raw_topic).strip().replace(" ", "-") or raw_topic
        project_path = None  # greenfield never starts on an existing path
        topic_clean = f"new project: {new_project_name}"

    self_mod = bool(
        project_path
        and project_path.resolve() == Path(__file__).resolve().parent.resolve()
    )

    session = design_partner.start_for_ws(ws, project_path, topic_clean, self_mod=self_mod)
    if new_project_name:
        session.new_project_name = new_project_name
    target_desc = str(project_path) if project_path else (
        f"(new project: {new_project_name})" if new_project_name else "(no project)"
    )
    log.info(
        f"design_partner: session {session.id} started on {target_desc} "
        f"(topic={topic_clean!r}, self_mod={self_mod}, prompted_for_topic={topic_was_empty}, "
        f"new_project={bool(new_project_name)})"
    )

    await session.emit_state()
    await session.emit("design.topic_set", title=topic_clean, status="done",
                        payload={"project_path": str(project_path) if project_path else ""})

    if topic_was_empty:
        # Explicit opt-in via _DESIGN_OPTIN_PHRASES — user hasn't named a
        # topic yet. Ask for one. The next user utterance routes through the
        # DESIGNING branch (voice_handler) directly to Opus, which will
        # absorb it as the first design-conversation message.
        msg = "What would you like to design, sir?"
    elif new_project_name:
        msg = f"New project, sir, {new_project_name}. What stack are we using?"
    elif not project_path:
        msg = f"Right, sir, designing '{topic_clean}' in the abstract. Open a project before shipping."
    elif self_mod:
        msg = f"Right, sir, let's design '{topic_clean}' for myself. I'll be careful."
    else:
        msg = f"Right, sir, let's design '{topic_clean}' for {project_path.name}."
    await _speak(ws, msg)


async def _execute_start_dictation(ws):
    """Mode 2 (chunk 21) — open dictation mode.

    Verifies a project is open (dictation needs a target). Sets the
    voice_handler's dictation_phase to "capturing_prompt" so the next
    user utterance is captured verbatim as the prompt rather than
    routed through the normal action pipeline. Speaks the entry line
    and flips the Process Panel's · dictation indicator on.

    The phase is owned by voice_handler — it lives on the ws object
    via setattr (no nicer option for cross-call state without a class
    rewrite). Cleared on confirm/cancel or on a fresh dictation start.
    """
    import project_context

    active_ctx = project_context.get_active()
    project_path = active_ctx.project_path if active_ctx else None
    if project_path is None:
        await _speak(
            ws,
            "Which project should I dictate to, sir? Open one first, then say it again.",
        )
        return

    # Stash state on ws so the transcript handler can find it next turn.
    ws.dictation_phase = "capturing_prompt"
    ws.dictation_captured_prompt = ""
    ws.dictation_project_path = str(project_path)

    log.info(
        "dictation: opened for project=%s — awaiting prompt utterance",
        project_path.name,
    )

    # Push the panel indicator on via a dedicated WS event the frontend
    # mirrors into Process Panel header chrome. (See main.ts hook.)
    try:
        await ws.send_json({
            "type": "dictation_event",
            "event": {"state": "capturing_prompt", "project": project_path.name},
        })
    except Exception:
        pass

    await _speak(ws, "Dictating to Claude Code, sir. What would you like to say?")


async def _execute_confirm_dictation(ws, prompt: str):
    """Mode 2 — user confirmed; auto-paste to Cursor's claude terminal.

    Uses the same paste_into_cursor_claude helper as design ship-it
    (Mode 1). On pre-flight failure (Cursor not focused), falls back to
    writing .valet/inbox/<task_id>.md so the user still has the prompt
    staged.

    Clears ws.dictation_* state on exit (success OR failure).
    """
    from actions import paste_into_cursor_claude
    import uuid

    project_path_str = getattr(ws, "dictation_project_path", "")
    project_path = Path(project_path_str) if project_path_str else None

    # Wipe state up front so an error path doesn't leave a half-active
    # dictation session.
    ws.dictation_phase = None
    ws.dictation_captured_prompt = ""
    try:
        ws.dictation_project_path = ""
    except Exception:
        pass
    try:
        await ws.send_json({"type": "dictation_event", "event": {"state": "idle"}})
    except Exception:
        pass

    result = await paste_into_cursor_claude(prompt)

    if result.get("success"):
        log.info("dictation: paste succeeded (prompt_len=%d)", len(prompt))
        await _speak(ws, "Sent, sir.")
        return

    # Fallback path — write the prompt to .valet/inbox/<id>.md so the
    # user can paste manually. Mirrors the chunk-21 Mode 1 fallback.
    reason = result.get("reason", "unknown")
    detail = result.get("detail", "")
    log.warning(
        "dictation paste failed (reason=%s, detail=%s) — staging file fallback",
        reason, detail[:120],
    )

    inbox_path = None
    if project_path:
        try:
            inbox_dir = project_path / ".valet" / "inbox"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            stem = uuid.uuid4().hex[:8]
            inbox_path = inbox_dir / f"dictation-{stem}.md"
            inbox_path.write_text(prompt + "\n", encoding="utf-8")
        except Exception as e:
            log.error("dictation fallback write failed: %s", e)
            inbox_path = None

    if reason == "cursor_not_focused":
        front = result.get("frontmost", "another app")
        msg = (f"Prompt staged, sir — {front} was in focus, not Cursor. "
               f"Paste manually when ready.")
    else:
        msg = "Prompt staged, sir — paste manually if Cursor wasn't ready."
    if inbox_path is None:
        msg = ("Couldn't paste OR stage, sir — Cursor wasn't focused and "
               "I had no project to fall back to.")
    await _speak(ws, msg)


async def _execute_cancel_dictation(ws):
    """Mode 2 — user said cancel/scrap/nevermind. Drop the dictation state
    and speak an acknowledgment. No file write, no paste."""
    ws.dictation_phase = None
    ws.dictation_captured_prompt = ""
    try:
        ws.dictation_project_path = ""
    except Exception:
        pass
    try:
        await ws.send_json({"type": "dictation_event", "event": {"state": "idle"}})
    except Exception:
        pass
    await _speak(ws, "Cancelled, sir.")


async def _execute_dispatch_to_agent(ws, agent_raw: str, task: str):
    """Voice fast-path for "use the X agent to <task>".

    Normalizes the loosely-recognized agent name against the live agent
    list (so "general purpose" matches "general-purpose"). On hit, composes
    the dispatch-header + task and pastes it into the active Cursor window.
    On miss, lists available agents back to the user so they can retry.
    """
    import agents
    available = agents.list_agents(Path(__file__).parent)
    norm = agent_raw.lower().replace(" ", "-").replace("_", "-")

    matched: Optional[dict] = None
    # Pass 1: exact normalized match
    for a in available:
        if a["name"].lower() == norm:
            matched = a
            break
    # Pass 2: case-insensitive raw match (handles "Plan" said as "plan")
    if not matched:
        for a in available:
            if a["name"].lower() == agent_raw.lower():
                matched = a
                break
    # Pass 3: substring (so "explore" matches "Explore")
    if not matched:
        for a in available:
            if norm in a["name"].lower() or a["name"].lower() in norm:
                matched = a
                break

    if not matched:
        names = ", ".join(a["name"] for a in available[:6])
        await _speak(ws, f"I don't know that agent, sir. Try one of: {names}.")
        return

    prompt = agents.format_dispatch_header(matched["name"]) + task
    log.info(
        "_execute_dispatch_to_agent: agent=%r task_len=%d → pasting",
        matched["name"], len(task),
    )
    from actions import paste_into_cursor_claude
    result = await paste_into_cursor_claude(prompt)
    if result.get("success"):
        await _speak(ws, f"Dispatched to the {matched['name']} agent, sir.")
    else:
        reason = result.get("reason", "unknown")
        await _speak(ws, f"Couldn't reach Cursor to dispatch, sir. {reason}.")


def _ship_sent_line(verified: bool) -> str:
    """Spoken confirmation after a paste-ship, honest about verification.

    `verified` comes from paste_into_cursor_claude — True only when we
    confirmed the integrated terminal actually held focus for the paste. When
    we couldn't confirm, say so instead of the old false "Sent to Claude Code,
    sir" that fired whenever osascript merely exited 0.
    """
    if verified:
        return "Sent to Claude Code, sir."
    return (
        "Pasted, sir, but I couldn't confirm it reached the Claude pane. "
        "Have a glance to be sure."
    )


def _resolve_ship_prompt(session) -> tuple[str, Optional[str]]:
    """Compose the final ship prompt + the agent it should dispatch to.

    Reads `session.agent` (explicit pick from the design panel dropdown or
    a voice "use the X agent" command). If unset, runs the keyword-based
    auto-detect on the draft text — falls back to no agent prefix on miss.

    Returns (final_prompt_with_optional_header, agent_name_or_None) so
    callers can log which agent fired.
    """
    import design_partner, agents
    final = design_partner.compose_final_prompt(session)

    agent_name = (session.agent or "").strip() or None
    if not agent_name:
        try:
            available = agents.list_agents(Path(__file__).parent)
            agent_name = agents.auto_detect_agent(final, available)
        except Exception as e:
            log.debug(f"_resolve_ship_prompt: auto_detect_agent failed: {e}")
            agent_name = None

    if agent_name:
        final = agents.format_dispatch_header(agent_name) + final

    return final, agent_name


async def _execute_ship_design(ws):
    """Phase 4 — DESIGNING → BUILDING. Compose final prompt + hand off.

    Two dispatch methods, picked by config/design_partner.json#ship_method:

      file        — write to <project>/.valet/inbox/<id>.md, speak the path,
                    transition to BUILDING. Safe, deterministic, default.
      applescript — bring Cursor to front, ask for explicit voice confirmation
                    ("ship it for real"), then clipboard-paste + Enter into
                    whatever pane has focus. Brittle — confirmation gate is
                    NOT optional.

    Phase 5 self-mod approval gate: when session.self_mod is True, we DON'T
    ship immediately. Instead we stage a "self_mod_confirm" pending offer
    and require the explicit voice word "confirmed" before any composition
    or dispatch happens. NOT optional per the plan.
    """
    import design_partner
    self_mod = _load_self_mod()  # None in a shipped build → self-mod disabled

    session = design_partner.get_for_ws(ws)
    if session is None:
        log.info("_execute_ship_design: no active session on ws")
        await _speak(ws, "No design to ship, sir.")
        return

    log.info(
        "_execute_ship_design: session=%s has_target=%s draft_empty=%s self_mod=%s",
        session.id, session.has_target, session.draft.is_empty(), session.self_mod,
    )

    if session.draft.is_empty():
        await _speak(ws, "The draft is empty, sir. Nothing to ship yet.")
        return

    # Greenfield branch — user picked "+ New project" in the dropdown or
    # said "new project for X". Scaffold the directory (git init,
    # stack-aware .gitignore, manifest, initial commit) BEFORE the paste so
    # Cursor is open and the claude pane is ready when paste_into_cursor_claude
    # fires. Once scaffolding succeeds, session.project_path is set and the
    # flow falls through to the regular auto_paste branch — which knows how
    # to window-target by project path. Greenfield is never self-mod (the
    # new project is not the VALET repo), so the self-mod gate skips.
    if session.is_greenfield:
        from actions import new_cursor_project
        await _speak(ws, f"Scaffolding {session.new_project_name}, sir.")
        try:
            result = await new_cursor_project(
                name=session.new_project_name,
                base_dir=session.new_project_base_dir,
                stack=session.stack,
            )
        except Exception as e:
            log.error(f"new_cursor_project failed: {e}")
            await _speak(ws, f"Couldn't scaffold the project, sir: {str(e)[:120]}")
            return
        if not result.get("success"):
            await _speak(ws, result.get("confirmation", "Scaffold failed, sir."))
            return
        # Promote the new path to the session's real target so the rest of
        # the ship flow treats it like any other existing project.
        session.project_path = Path(result["path"])
        session.new_project_name = None
        session.new_project_base_dir = None
        await session.emit_state()
        # Brief beat so Cursor's claude pane has time to spin up before
        # paste_into_cursor_claude tries to AXRaise the window.
        await asyncio.sleep(1.2)
        # Fall through to the auto_paste branch below.

    # No-target fast path: paste the draft straight into Cursor's claude
    # terminal without project bookkeeping. The user explicitly wants to ship
    # a prompt into whatever Cursor pane is in focus, even when no VALET
    # project is the formal "target".
    if not session.has_target:
        from actions import paste_into_cursor_claude
        final_prompt, dispatch_agent = _resolve_ship_prompt(session)
        log.info(
            "_execute_ship_design: no-target paste, prompt_len=%d, agent=%r",
            len(final_prompt), dispatch_agent,
        )
        # No target selected → no window targeting; paste lands in whichever
        # Cursor window is currently active.
        result = await paste_into_cursor_claude(final_prompt)
        log.info("_execute_ship_design: paste result=%s", result)
        if result.get("success"):
            session.mark_building()
            await session.emit_state()
            await _speak(ws, _ship_sent_line(result.get("verified", False)))
        else:
            reason = result.get("reason", "unknown")
            detail = result.get("detail", "")
            if reason == "cursor_unavailable":
                await _speak(ws, "Couldn't reach Cursor, sir. Make sure it's running.")
            elif reason == "cursor_focus_lost":
                await _speak(ws, "Cursor wouldn't take focus, sir. Try again.")
            else:
                await _speak(ws, f"Couldn't paste, sir. {detail[:120] or reason}.")
        return

    # ── Phase 5 approval gate for self-modifications ──
    # Belt-and-suspenders: check both the session's self_mod flag AND the
    # path identity (in case the flag got out of sync somehow).
    if self_mod is not None and (session.self_mod or self_mod.is_valet_repo(session.project_path)):
        ws.pending_offer = {
            "kind": "self_mod_confirm",
            "session_id": session.id,
        }
        await _speak(
            ws,
            "I'm about to modify myself, sir. Say 'confirmed' to proceed, "
            "anything else to cancel."
        )
        return

    final_prompt, dispatch_agent = _resolve_ship_prompt(session)
    log.info("_execute_ship_design: with-target ship, agent=%r", dispatch_agent)
    method = design_partner.get_ship_method()

    if method == "auto_paste":
        # Chunk 21 Mode 1: bring Cursor to front (pre-flight checks it's
        # already frontmost), clipboard-paste the prompt, press Enter. The
        # helper handles clipboard save/restore so the user's clipboard
        # isn't clobbered. On any pre-flight or AppleScript failure, fall
        # back to the file path — the prompt is staged at .valet/inbox/
        # so the user can paste manually.
        # Pass the project path so paste_into_cursor_claude can pick the
        # right Cursor window across multi-window setups (multi-monitor).
        from actions import paste_into_cursor_claude
        result = await paste_into_cursor_claude(
            final_prompt,
            target_project_path=str(session.project_path) if session.project_path else None,
        )

        if result.get("success"):
            session.mark_building()
            await session.emit_state()
            design_partner.persist(
                session, status="building",
                final_prompt=final_prompt, ship_method="auto_paste",
            )
            await _speak(ws, _ship_sent_line(result.get("verified", False)))
            return

        # Pre-flight or paste failed — fall through to file fallback with
        # an explanatory voice line.
        reason = result.get("reason", "unknown")
        detail = result.get("detail", "")
        log.warning(
            "auto_paste failed (reason=%s, detail=%s) — falling back to file ship",
            reason, detail[:120],
        )
        try:
            out = design_partner.ship_via_file(session, final_prompt)
        except Exception as e:
            log.error(f"ship_via_file fallback also failed: {e}")
            await _speak(ws, f"Couldn't stage the prompt, sir: {str(e)[:120]}")
            return

        session.mark_building()
        await session.emit_state()
        design_partner.persist(
            session, status="building",
            final_prompt=final_prompt, ship_method="auto_paste_fallback_file",
            inbox_path=str(out),
        )

        if reason == "cursor_not_focused":
            front = result.get("frontmost", "another app")
            await _speak(
                ws,
                f"Prompt staged, sir — {front} was in focus, not Cursor. "
                f"Paste manually when ready.",
            )
        else:
            await _speak(
                ws,
                "Prompt staged, sir — paste manually if Cursor wasn't ready.",
            )
        return

    if method == "applescript":
        # Two-step: prep + voice confirmation via the existing pending_offer
        # infrastructure. The actual paste happens after the user says
        # "ship it for real" — handled in _handle_pending_offer.
        ws.pending_offer = {
            "kind": "ship_confirm",
            "session_id": session.id,
            "final_prompt": final_prompt,
            "project_path": str(session.project_path),
        }
        await _speak(
            ws,
            f"Bringing Cursor forward, sir. Focus the claude terminal pane, "
            f"then say 'ship it for real' to paste. Cancel by saying 'never mind'."
        )
        # Pre-focus Cursor so the user can confirm focus immediately.
        try:
            import asyncio as _aio
            await _aio.create_subprocess_exec(
                "osascript", "-e", 'tell application "Cursor" to activate',
                stdout=_aio.subprocess.DEVNULL, stderr=_aio.subprocess.DEVNULL,
            )
        except Exception:
            pass
        return

    # Default: file method
    try:
        out = design_partner.ship_via_file(session, final_prompt)
    except Exception as e:
        log.error(f"ship_via_file failed: {e}")
        await _speak(ws, f"Couldn't stage the prompt, sir: {str(e)[:120]}")
        return

    session.mark_building()
    await session.emit_state()
    design_partner.persist(
        session, status="building",
        final_prompt=final_prompt, ship_method="file",
        inbox_path=str(out),
    )

    rel = out.relative_to(session.project_path) if session.project_path else out
    await _speak(
        ws,
        f"Prompt staged at {rel}, sir. Paste it into Cursor's claude terminal to ship."
    )


async def _handle_self_mod_confirm(transcript: str, ws) -> bool:
    """Pending-offer handler for the Phase 5 self-mod approval gate.

    ONLY accepts the literal word "confirmed" (allowing surrounding filler
    like "yes confirmed" or "confirmed please"). Anything else cancels —
    'yes' alone is NOT enough, by design.
    """
    offer = getattr(ws, "pending_offer", None)
    if not offer or offer.get("kind") != "self_mod_confirm":
        return False

    t = transcript.lower().strip()
    import re as _conf_re
    confirmed = bool(_conf_re.search(r"\bconfirmed\b", t))
    if not confirmed:
        await _speak(ws, "Cancelled, sir. Self-mod requires the word 'confirmed'.")
        return True

    import design_partner
    self_mod = _load_self_mod()
    if self_mod is None:
        await _speak(ws, "Self-modification isn't available in this build, sir.")
        return True

    session = None
    for s in design_partner._active.values():
        if s.id == offer["session_id"]:
            session = s
            break
    if session is None:
        await _speak(ws, "I lost the session, sir. Try again.")
        return True

    # Self-mod safety loop: snapshot any in-flight work, cut a feature/<topic>
    # branch, THEN paste the prompt so Claude Code builds on the branch. This is
    # what makes the BUILDING panel's "Merge to main" / "Scrap branch" buttons
    # and the "merge it" / "scrap it" voice commands mean something — you review
    # the self-mod on its branch and fold it in or throw it away cleanly. The
    # earlier surprise ("it started building a branch") was really the paste
    # failing on top of the branch; now that the paste lands, the branch is the
    # point. Auto-snapshot keeps create_feature_branch's clean-tree assert happy.
    try:
        snapshot_sha = self_mod.commit_wip_snapshot(session.topic)
    except Exception as e:
        log.error(f"commit_wip_snapshot failed: {e}")
        await _speak(ws, f"Couldn't snapshot the working tree, sir: {str(e)[:160]}")
        return True
    if snapshot_sha:
        log.info(f"_handle_self_mod_confirm: snapshotted dirty tree as {snapshot_sha[:8]}")

    parent_branch = self_mod.current_branch()  # where 'scrap it' returns to
    try:
        branch, pre_sha = self_mod.create_feature_branch(session.topic)
    except RuntimeError as e:
        await _speak(ws, f"Couldn't branch, sir: {str(e)[:200]}")
        return True
    session.feature_branch = branch
    session.pre_build_sha = pre_sha
    session.parent_branch = parent_branch
    log.info(
        "_handle_self_mod_confirm: on branch %s (from %s, pre_sha=%s)",
        branch, parent_branch, (pre_sha or "")[:8],
    )

    final_prompt, dispatch_agent = _resolve_ship_prompt(session)
    log.info("_handle_self_mod_confirm: composing ship on %s, agent=%r", branch, dispatch_agent)
    from actions import paste_into_cursor_claude
    paste_result = await paste_into_cursor_claude(
        final_prompt,
        target_project_path=str(session.project_path),
    )
    log.info("_handle_self_mod_confirm: paste result=%s", paste_result)

    if paste_result.get("success"):
        session.mark_building()
        await session.emit_state()
        design_partner.persist(
            session, status="building",
            final_prompt=final_prompt, ship_method="auto_paste-self-mod",
        )
        sent = _ship_sent_line(paste_result.get("verified", False))
        await _speak(
            ws,
            f"{sent} You're on a fresh branch — say 'merge it' to fold into "
            f"main once it checks out, or 'scrap it' to abandon it."
        )
        return True

    # Paste failed — fall back to file ship so the prompt isn't lost. The branch
    # is already cut, so the user can paste the staged prompt manually and still
    # use the merge/scrap loop.
    log.warning(
        "_handle_self_mod_confirm: paste failed (reason=%s), falling back to file ship",
        paste_result.get("reason"),
    )
    try:
        out = design_partner.ship_via_file(session, final_prompt)
    except Exception as e:
        await _speak(ws, f"Self-mod ship failed: {str(e)[:200]}")
        return True

    session.mark_building()
    await session.emit_state()
    design_partner.persist(
        session, status="building",
        final_prompt=final_prompt, ship_method="auto_paste_fallback_file-self-mod",
        inbox_path=str(out),
    )

    rel = out.relative_to(session.project_path) if session.project_path else out
    reason = paste_result.get("reason", "unknown")
    await _speak(
        ws,
        f"Branched, sir, but I couldn't paste ({reason}) — prompt staged at {rel}. "
        f"Copy it into the Claude pane; then 'merge it' or 'scrap it' as usual."
    )
    return True


async def _handle_ship_confirm(transcript: str, ws) -> bool:
    """Pending-offer handler for AppleScript ship method.

    Triggered after _execute_ship_design (method=applescript) staged the
    prompt and asked for voice confirmation. Recognizes:
      'ship it for real' / 'do it' / 'paste it' / 'go ahead' → paste
      cancel words (handled by _handle_pending_offer upstream) → drop offer

    Returns True if handled (offer consumed), False otherwise.
    """
    offer = getattr(ws, "pending_offer", None)
    if not offer or offer.get("kind") != "ship_confirm":
        return False

    t = transcript.lower().strip()
    confirm = (
        "ship it for real" in t or t == "for real" or
        "paste it" in t or t == "do it" or "go ahead" in t
    )
    if not confirm:
        return False

    import design_partner
    session = None
    for s in design_partner._active.values():
        if s.id == offer["session_id"]:
            session = s
            break
    if session is None:
        await _speak(ws, "I lost the session, sir. Try again.")
        return True

    ok = await design_partner.ship_via_applescript(session, offer["final_prompt"])
    if not ok:
        await _speak(ws, "AppleScript paste failed, sir. Falling back to file method.")
        try:
            out = design_partner.ship_via_file(session, offer["final_prompt"])
            design_partner.persist(
                session, status="building",
                final_prompt=offer["final_prompt"],
                ship_method="applescript-fallback-file",
                inbox_path=str(out),
            )
            session.mark_building()
            await session.emit_state()
            await _speak(ws, f"Staged at .valet/inbox/{out.name} instead.")
        except Exception as e:
            log.error(f"applescript fallback file write failed: {e}")
        return True

    session.mark_building()
    await session.emit_state()
    design_partner.persist(
        session, status="building",
        final_prompt=offer["final_prompt"], ship_method="applescript",
        inbox_path="",
    )
    await _speak(ws, "Pasted, sir.")
    return True


async def _execute_scrap_design(ws):
    """DESIGNING → IDLE. Drops the draft and the design conversation history.

    Once the session has transitioned to BUILDING (shipped), scrap is a
    no-op + clarification — the inbox file (if any) is the user's now and
    VALET doesn't delete it. To clean up an in-progress build the user
    deletes the inbox file manually.
    """
    import design_partner

    session = design_partner.get_for_ws(ws)
    if session is None:
        await _speak(ws, "No design to scrap, sir.")
        return

    if session.state == "BUILDING":
        # Shipped self-mod → "Scrap branch" should abandon the feature branch
        # (discard the build, return to where we branched from). Only do this
        # when we're actually still on that branch; otherwise it already merged
        # or was a file-ship, and the old keep-the-inbox guidance applies.
        self_mod = _load_self_mod()
        branch = getattr(session, "feature_branch", None)
        parent = getattr(session, "parent_branch", None) or "main"
        if self_mod and branch and self_mod.current_branch() == branch:
            res = self_mod.abandon_feature_branch(branch, return_to=parent)
            if res["success"]:
                design_partner.persist(session, status="scrapped",
                                       final_prompt=session.draft.render_markdown())
                session.scrap()
                await session.emit_state()
                design_partner.stop_for_ws(ws)
                await _speak(ws, f"Scrapped, sir. {res['message']} Clean slate.")
            else:
                await _speak(ws, f"Couldn't scrap the branch, sir: {res['message'][:200]}")
            return
        await _speak(ws, "That one already shipped, sir. The inbox file is yours to keep or delete.")
        return

    design_partner.persist(session, status="scrapped", final_prompt=session.draft.render_markdown())
    session.scrap()
    await session.emit_state()
    design_partner.stop_for_ws(ws)
    await _speak(ws, "Scrapped, sir. Clean slate.")


async def _execute_merge_branch(ws):
    """Phase 5 — run smoke_test.sh then merge the current feature/* branch into main.

    Refuses if not on a feature/* branch. Refuses if smoke fails (without
    auto-resetting — user decides what to do with a failed feature branch).
    Never deletes the feature branch after merge.
    """
    self_mod = _load_self_mod()
    if self_mod is None:
        await _speak(ws, "Branch merging isn't available in this build, sir.")
        return
    cur = self_mod.current_branch()
    if not cur.startswith("feature/"):
        await _speak(ws, f"Not on a feature branch, sir. Currently on {cur}. Nothing to merge.")
        return

    await _speak(ws, "Running smoke test, sir.")
    result = await self_mod.run_smoke_test(timeout_sec=120)
    if not result["success"]:
        last = (result["stdout"] + result["stderr"]).splitlines()
        tail = " ".join(last[-3:])[:300] if last else "no output"
        await _speak(ws, f"Smoke failed, sir. Staying on {cur}. Tail: {tail}")
        log.warning(f"smoke fail on merge_branch:\nstdout:\n{result['stdout']}\nstderr:\n{result['stderr']}")
        return

    merge = self_mod.merge_to_main(cur)
    if merge["success"]:
        await _speak(ws, f"Smoke passed. {merge['message']} You may want to restart yourself.")
    else:
        await _speak(ws, f"Smoke passed but merge failed: {merge['message'][:200]}")


async def _execute_restart_self(ws):
    """Restart the backend. Speaks confirmation BEFORE the process goes away
    (otherwise the speech doesn't make it to the user). Lives in restart.py (not
    self_mod) so it works in packaged builds too — see restart.restart_self."""
    import restart
    await _speak(ws, "Restarting in a couple seconds, sir.")
    # Give the TTS time to actually send before the process is replaced.
    await asyncio.sleep(0.8)
    result = restart.restart_self()
    if not result["success"]:
        await _speak(ws, f"Restart failed: {result['message'][:200]}")


async def _execute_show_draft(ws):
    """Speak the assembled draft so far."""
    import design_partner

    session = design_partner.get_for_ws(ws)
    if session is None:
        await _speak(ws, "No design in progress, sir.")
        return
    if session.draft.is_empty():
        await _speak(ws, "The draft is empty so far, sir.")
        return

    # The full markdown can be long — speak a short summary, panel has the full text.
    bits = []
    if session.draft.goal:
        bits.append(f"Goal: {session.draft.goal[:140]}")
    if session.draft.constraints:
        bits.append(f"Constraints: {session.draft.constraints[:120]}")
    if session.draft.open_questions:
        bits.append(f"{len(session.draft.open_questions)} open question{'s' if len(session.draft.open_questions) != 1 else ''}")
    spoken = "Current draft, sir. " + ". ".join(bits) + ". Full text is in the panel."
    await _speak(ws, spoken)


async def _execute_refresh_context(target: str, ws):
    """Re-read warm context for the active project (or a named one)."""
    import project_context
    name = target.strip()
    path = None
    if name:
        from memory import resolve_project
        resolved = resolve_project(name)
        if resolved:
            path = Path(resolved)
        else:
            msg = f"I don't have '{name}' on file as an open project, sir."
            audio = await synthesize_speech(msg)
            if audio and ws:
                try:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                except Exception:
                    pass
            return

    try:
        ctx = await project_context.refresh(path)
        if ctx is None:
            msg = "No active project to refresh, sir — open one first."
        else:
            msg = f"Context refreshed for {ctx.project_path.name}, sir."
    except Exception as e:
        log.error(f"refresh_context failed: {e}")
        msg = "Something went wrong refreshing context, sir."

    audio = await synthesize_speech(msg)
    if audio and ws:
        try:
            await ws.send_json({"type": "status", "state": "speaking"})
            await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
        except Exception:
            pass


def _format_projects_for_voice(projects: list[dict]) -> str:
    """Render a known-projects list as a single-sentence butler reply."""
    if not projects:
        return "No projects on the list, sir."
    n = len(projects)
    sample = ", ".join(p["name"] for p in projects[:5])
    if n == 1:
        return f"One project, sir: {sample}."
    if n <= 5:
        return f"{n} projects, sir: {sample}."
    return f"{n} projects, sir. The latest are: {sample}, and {n - 5} more."


def _format_contacts_for_prompt(limit: int = 50) -> str:
    """The saved address book as 'Name — email' lines for the system prompt.
    Capped so a large book doesn't bloat context."""
    try:
        contacts = list_contacts()
    except Exception:
        contacts = []
    if not contacts:
        return "(none saved yet)"
    lines = [f"- {c['name']} — {c['email']}" for c in contacts[:limit]]
    if len(contacts) > limit:
        lines.append(f"…and {len(contacts) - limit} more.")
    return "\n".join(lines)


async def _resolve_contact_email(name: str) -> dict | None:
    """Resolve a spoken name to {name, email, source}. VALET's profile store
    first, then Apple Contacts (if access was granted). None if unknown or
    ambiguous — the caller asks instead of guessing."""
    hit = find_contact(name)
    if hit:
        return {"name": hit["name"], "email": hit["email"], "source": "profile"}
    try:
        if contacts_access.has_access():
            ac = await contacts_access.find_one(name)
            if ac:
                return {"name": ac["name"], "email": ac["email"], "source": "apple"}
    except Exception as e:
        log.warning(f"apple contacts resolve failed: {e}")
    return None


async def _execute_save_contact(target: str, ws):
    """Save a contact from a SAVE_CONTACT tag. target: "name ||| email"."""
    name, _, email = target.partition("|||")
    name, email = name.strip(), email.strip()
    if not name or "@" not in email:
        await _speak(ws, "I need a name and an email to save a contact, sir.")
        return
    ok = add_contact(name, email)
    await _speak(ws, f"Saved {name}, sir." if ok else f"I couldn't save {name}, sir.")


async def _execute_draft_email(target: str, ws):
    """Create a Gmail draft from a DRAFT_EMAIL action tag.

    target format: "to ||| subject ||| body"
    Optional 4th and 5th fields are cc and bcc.
    """
    parts = [p.strip() for p in target.split("|||")]
    if len(parts) < 3:
        # Validation failure — short-circuit, no task event.
        msg = "I need a recipient, subject, and body, sir."
        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass
        return

    to, subject, body = parts[0], parts[1], parts[2]
    cc = parts[3] if len(parts) > 3 else ""
    bcc = parts[4] if len(parts) > 4 else ""

    # Drafting writes to Gmail, which is an opt-in integration that isn't wired up
    # yet (Google/Gmail is deferred). If it isn't connected, fail honestly instead
    # of claiming a "re-authentication" problem with an account that was never set
    # up. Apple Mail is read-only by design, so there's no local draft path either.
    try:
        import google_auth
        gmail_ready = google_auth.is_connected()
    except Exception:
        gmail_ready = False
    if not gmail_ready:
        msg = ("I can't draft emails yet, sir — connect Gmail under "
               "Settings, Console Settings, Accounts.")
        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass
        return

    # Resolve a NAME recipient ("email Nick") to a real address — VALET's profile
    # store first, then Apple Contacts. NEVER fabricate: if it's a name we can't
    # resolve, ask (the user can save it) rather than guessing an address. An
    # explicit address (already contains "@") is used as-is.
    if to and "@" not in to:
        hit = await _resolve_contact_email(to)
        if hit:
            log.info(f"resolved recipient {to!r} -> {hit['email']} ({hit['source']})")
            to = hit["email"]
        else:
            await _speak(
                ws,
                f"I don't have an email for {to}, sir. What's the address? "
                f"You can say, save {to} as their address.",
            )
            return

    async with process_bus.task_context(f"Drafting email to {to}", detail=subject[:80]) as task_id:
        try:
            await emit_step(task_id, "Creating Gmail draft…", status="active")
            draft = await mail_create_draft(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
            if draft:
                msg = f"Draft saved to {to}, sir — check your Drafts folder to review and send."
                await emit_step(task_id, "Draft saved", detail=subject[:120], status="done")
            else:
                msg = "I couldn't save that draft, sir — Gmail may need re-authentication for draft access."
                await emit_error(task_id, "Draft not saved", detail="Gmail returned no result; reauth may be needed.")
        except Exception as e:
            log.error(f"draft_email failed: {e}")
            await emit_error(task_id, "Draft email failed", detail=str(e)[:200])
            msg = "Something went wrong saving that draft, sir."

        audio = await synthesize_speech(msg)
        if audio and ws:
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass


async def _execute_check_date(target: str, ws):
    """Look up calendar events for a specific date and read them back."""
    import apple_calendar
    date_str = target.strip()
    if not date_str:
        msg = "Which date, sir?"
    elif apple_calendar.auth_status() != 3 and not google_auth.is_connected():
        msg = ("I don't have Calendar access yet, sir — grant it under Settings, "
               "Permissions, Calendar, or connect Google in Settings.")
    else:
        try:
            events = await _read_calendar_merged(date_str)
            if not events:
                msg = f"You have nothing on the calendar for {date_str}, sir."
            else:
                lines = []
                for e in events[:6]:
                    if e.get("all_day"):
                        lines.append(f"{e['title']} all day")
                    else:
                        lines.append(f"{e['title']} at {e.get('time_str') or 'unknown time'}")
                more = f" And {len(events) - 6} more." if len(events) > 6 else ""
                msg = f"On {date_str}: " + "; ".join(lines) + "." + more
        except Exception as e:
            log.error(f"check_date failed: {e}")
            msg = "Something went wrong checking that date, sir."

    audio = await synthesize_speech(msg)
    if audio and ws:
        try:
            await ws.send_json({"type": "status", "state": "speaking"})
            await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
        except Exception:
            pass


async def _execute_browse(target: str):
    """Execute a browse action from an LLM-embedded [ACTION:BROWSE] tag."""
    async with process_bus.task_context(f"Opening {target[:60]}") as task_id:
        try:
            if target.startswith("http") or "." in target.split()[0]:
                url = target
            else:
                from urllib.parse import quote
                url = f"https://www.google.com/search?q={quote(target)}"
            await emit_browser_action(task_id, "open in browser", url=url)
            await open_browser(url)
        except Exception as e:
            log.error(f"Browse execution failed: {e}")
            await emit_error(task_id, "Browse failed", detail=str(e)[:200])


async def _focus_terminal_window(project_name: str):
    """Bring a Terminal window matching the project name to front."""
    escaped = project_name.replace('"', '\\"')
    script = f'''
tell application "Terminal"
    repeat with w in windows
        if name of w contains "{escaped}" then
            set index of w to 1
            activate
            exit repeat
        end if
    end repeat
end tell
'''
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        pass


async def _execute_open_terminal():
    """Execute an open-terminal action from an LLM-embedded [ACTION:OPEN_TERMINAL] tag."""
    try:
        await handle_open_terminal()
    except Exception as e:
        log.error(f"Open terminal failed: {e}")


async def _execute_open_app(target: str):
    """Wrap an [ACTION:OPEN_APP] call in a panel task_context so the launch
    shows up as a row in the process panel."""
    async with process_bus.task_context(f"Opening {target[:60]}") as task_id:
        try:
            await open_app_or_path(target, task_id=task_id)
        except Exception as e:
            log.error(f"open_app failed: {e}")
            await emit_error(task_id, "Open app failed", detail=str(e)[:200])


async def _execute_open_url(url: str, browser: str = "chrome", label: str = ""):
    """Open a resolved web destination in the browser via `open -a` (no
    keystrokes / Accessibility). Backs the open_url fast-path for websites."""
    async with process_bus.task_context(f"Opening {label or url}"[:60]) as task_id:
        try:
            await emit_step(task_id, f"Opening {url}", status="active")
            result = await open_browser(url, browser=browser)
            if result.get("success", True):
                await emit_step(task_id, "Opened", detail=url, status="done")
            else:
                await emit_error(task_id, "Couldn't open", detail=result.get("confirmation", url))
        except Exception as e:
            log.error(f"open_url failed: {e}")
            await emit_error(task_id, "Open URL failed", detail=str(e)[:200])


async def _execute_open_note(query: str, ws):
    """Show a note in Notes.app (don't read it aloud). Runs inside a task_context
    so the process panel shows "Opening … note" and clears when done."""
    async with process_bus.task_context(f"Opening note: {query}"[:60]) as task_id:
        try:
            await emit_step(task_id, "Finding note…", status="active")
            note = await open_note(query)
            if note:
                title = note.get("title", query)
                await emit_app_launch(task_id, "Notes", status="done", detail=title)
                msg = f"Opened '{title}' in Notes, sir."
            else:
                await emit_error(task_id, "No matching note", detail=query[:120])
                msg = f"I couldn't find a note matching '{query}', sir."
        except Exception as e:
            log.error(f"open_note failed: {e}")
            await emit_error(task_id, "Open note failed", detail=str(e)[:200])
            msg = "Something went wrong opening that note, sir."
    await _speak(ws, msg)


async def _execute_type(target: str, press_enter: bool):
    """Wrap an [ACTION:TYPE] / [ACTION:SEND] call in a task_context so the
    typed text shows up in the process panel."""
    app = target.partition("|||")[0].strip() if "|||" in target else ""
    title = f"{'Sending' if press_enter else 'Typing'} in {app or 'active app'}"
    async with process_bus.task_context(title) as task_id:
        try:
            await type_into_app(target, press_enter=press_enter, task_id=task_id)
        except Exception as e:
            log.error(f"type_into_app failed: {e}")
            await emit_error(task_id, "Type failed", detail=str(e)[:200])


def _find_project_dir(project_name: str) -> str | None:
    """Find a project directory by name from cached projects or Desktop."""
    for p in cached_projects:
        if project_name.lower() in p.get("name", "").lower():
            return p.get("path")
    desktop = Path.home() / "Desktop"
    for d in desktop.iterdir():
        if d.is_dir() and project_name.lower() in d.name.lower():
            return str(d)
    return None


async def _execute_prompt_project(project_name: str, prompt: str, work_session: WorkSession, ws, dispatch_id: int = None, history: list[dict] = None, voice_state: dict = None):
    """Dispatch a prompt to Claude Code in a project directory.

    Runs entirely in the background. VALET returns to conversation mode
    immediately. When Claude Code finishes, VALET interrupts to report.
    """
    async with process_bus.task_context(f"Dispatching to {project_name}", detail=prompt[:120]) as task_id:
        try:
            project_dir = _find_project_dir(project_name)

            # Register dispatch if not already registered
            if dispatch_id is None:
                dispatch_id = dispatch_registry.register(project_name, project_dir or "", prompt)

            if not project_dir:
                await emit_error(task_id, f"Project '{project_name}' not found")
                msg = f"Couldn't find the {project_name} project directory, sir."
                audio = await synthesize_speech(msg)
                if audio and ws:
                    try:
                        await ws.send_json({"type": "status", "state": "speaking"})
                        await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                    except Exception:
                        pass
                return
            await emit_step(task_id, f"Found project: {project_name}", detail=project_dir)

            # Use a SEPARATE session so we don't trap the main conversation
            dispatch = WorkSession()
            await dispatch.start(project_dir, project_name)

            # Bring matching Terminal window to front so user can watch
            asyncio.create_task(_focus_terminal_window(project_name))

            log.info(f"Dispatching to {project_name} in {project_dir}: {prompt[:80]}")
            dispatch_registry.update_status(dispatch_id, "building")
            await emit_step(task_id, "Claude Code working…", status="active")

            # Run claude -p in background. WorkSession.send() emits tool.*
            # events as it streams stdout (see work_mode.py + claude_middleware.py
            # — middleware extracts result.* cards via Haiku after completion).
            full_response = await dispatch.send(prompt, task_id=task_id, anthropic_client=anthropic_client)
            await dispatch.stop()

            # Auto-open any localhost URLs from response
            import re as _re
            # Check for the explicit RUNNING_AT marker first
            running_match = _re.search(r'RUNNING_AT=(https?://localhost:\d+)', full_response or "")
            if not running_match:
                running_match = _re.search(r'https?://localhost:\d+', full_response or "")
            if running_match:
                url = running_match.group(1) if running_match.lastindex else running_match.group(0)
                asyncio.create_task(_execute_browse(url))
                log.info(f"Auto-opening {url}")
                # Store URL in dispatch
                if dispatch_id:
                    dispatch_registry.update_status(dispatch_id, "completed",
                        response=full_response[:2000], summary=f"Running at {url}")

            if not full_response or full_response.startswith("Hit a problem") or full_response.startswith("That's taking"):
                dispatch_registry.update_status(dispatch_id, "failed" if full_response else "timeout", response=full_response or "")
                msg = f"Sir, I ran into an issue with {project_name}. {full_response[:150] if full_response else 'No response received.'}"
                await emit_error(task_id, "Dispatch failed", detail=msg[:200])
            else:
                # Summarize via Haiku — don't read word for word
                if anthropic_client:
                    try:
                        summary = await anthropic_client.messages.create(
                            model="claude-haiku-4-5-20251001",
                            max_tokens=150,
                            system=(
                                "You are VALET reporting back on what you found or built in a project. "
                                "Speak in first person — 'I found', 'I built', 'I reviewed'. "
                                "Start with 'Sir, ' to get the user's attention. "
                                "Be specific but concise — highlight the key findings or actions taken. "
                                "If there are multiple items, give the count and top 2-3 briefly. "
                                "End by asking how the user wants to proceed. "
                                "NEVER read out URLs or localhost addresses. NEVER say 'Claude Code'. "
                                "2-3 sentences max. No markdown. Natural spoken voice."
                            ),
                            messages=[{"role": "user", "content": f"Project: {project_name}\nClaude Code reported:\n{full_response[:3000]}"}],
                        )
                        msg = summary.content[0].text
                    except Exception:
                        msg = f"Sir, {project_name} finished. Here's the gist: {full_response[:200]}"
                else:
                    msg = f"Sir, {project_name} is done. {full_response[:200]}"

            # Speak the result — skip if user has spoken recently to avoid audio collision
            log.info(f"Dispatch summary for {project_name}: {msg[:100]}")
            if voice_state and time.time() - voice_state["last_user_time"] < 3:
                log.info(f"Skipping dispatch audio for {project_name} — user spoke recently")
                # Result is still stored in history below so VALET can reference it
            else:
                audio = await synthesize_speech(strip_markdown_for_tts(msg))
                if ws:
                    try:
                        await ws.send_json({"type": "status", "state": "speaking"})
                        if audio:
                            await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                            log.info(f"Dispatch audio sent for {project_name}")
                        else:
                            await ws.send_json({"type": "text", "text": msg})
                            log.info(f"Dispatch text fallback sent for {project_name}")
                    except Exception as e:
                        log.error(f"Dispatch audio send failed: {e}")

            # Store dispatch result in conversation history so VALET remembers it
            if history is not None:
                history.append({"role": "assistant", "content": f"[Dispatch result for {project_name}]: {msg}"})

            dispatch_registry.update_status(dispatch_id, "completed", response=full_response[:2000], summary=msg[:200])
            log.info(f"Project {project_name} dispatch complete ({len(full_response)} chars)")

        except Exception as e:
            log.error(f"Prompt project failed: {e}", exc_info=True)
            await emit_error(task_id, "Dispatch crashed", detail=str(e)[:200])
            try:
                msg = f"Had trouble connecting to {project_name}, sir."
                audio = await synthesize_speech(msg)
                if audio and ws:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
            except Exception:
                pass


# Match any http(s) URL appearing in a code_execution.input.code Python
# source. Excludes whitespace, quotes, angle brackets, and the closing
# paren that often follows the URL in code. Stripping is done at the
# caller because trailing punctuation (`,`, `.`, `)`) is common in code
# but should not be part of the URL.
_CODE_URL_RE = re.compile(r'https?://[^\s"\'<>)]+')


def _extract_urls_from_code(code: str) -> list[str]:
    """Pull URLs out of a code_execution Python source string in order of
    appearance. Trailing code punctuation is stripped. Returns a list with
    duplicates preserved — the model may legitimately fetch the same URL
    twice across iterations."""
    if not code:
        return []
    urls: list[str] = []
    for m in _CODE_URL_RE.findall(code):
        clean = m.rstrip(",.;:)\"'")
        if clean.startswith(("http://", "https://")):
            urls.append(clean)
    return urls


async def _emit_research_source_card(task_id: str, url: str, snippet: str) -> None:
    """Fire-and-forget: fetch preview metadata, emit result.research_source.

    Never raises. If the preview fetch fails, the card still renders with
    hostname-only metadata and the snippet text.
    """
    try:
        preview = await fetch_page_preview(url)
        title = preview.get("title") or preview.get("hostname") or url
        await emit_tool_event(
            task_id,
            "result.research_source",
            title[:120],
            detail=(snippet or "")[:300],
            status="done",
            payload={
                "url": url,
                "title": preview.get("title"),
                "hostname": preview.get("hostname"),
                "og_image_url": preview.get("og_image_url"),
                "snippet": (snippet or "")[:500],
            },
        )
    except Exception as e:
        log.debug(f"emit_research_source_card failed for {url}: {e}")


def _extract_fetch_snippet(content) -> str:
    """Pull a short snippet from a web_fetch_tool_result.content list.

    The result content is typically a list of structured items; we look
    for any item with a `.text` attribute (or `text` key) and return the
    first ~300 chars of the first non-empty one.
    """
    if not isinstance(content, list):
        return ""
    for item in content:
        txt = getattr(item, "text", None)
        if txt is None and isinstance(item, dict):
            txt = item.get("text")
        if txt:
            s = str(txt).strip()
            # Take the first paragraph; collapse internal whitespace.
            first = re.split(r"\n\s*\n", s, maxsplit=1)[0]
            return re.sub(r"\s+", " ", first)[:500]
    return ""


@observability.observe(name="native-research", capture_input=False, capture_output=False)
async def _execute_native_research(target: str, ws=None):
    """Native research path — Opus 4.7 with server-side web_search + web_fetch.

    No subprocess. No folder. Tool calls stream to the Process Panel as
    tool.web_search / tool.web_fetch events. The completed response runs
    through the existing Haiku card-extraction middleware so result.web /
    result.product / result.location / result.image cards land in the panel.
    A short voice summary is spoken via TTS.

    Filesystem-readonly by design — see docs/research_routing_diagnosis.md.
    """
    log.info("native_research invoked: query=%r ws=%s", target[:160], "yes" if ws else "no")
    async with process_bus.task_context(f"Researching: {target[:60]}") as task_id:
        if anthropic_client is None:
            log.warning("native_research bypassed via fallback: reason=no_anthropic_client")
            await emit_error(task_id, "Research unavailable", detail="ANTHROPIC_API_KEY not configured.")
            return

        # Per-request 10-minute timeout. Default client timeout is 20s, but
        # server-side web_search + web_fetch agentic loops can easily run
        # for minutes — non-streamed requests at the SDK default hit the
        # docs.anthropic.com/en/api/errors#long-requests timeout. with_options
        # clones the client without mutating the shared global.
        client = anthropic_client.with_options(timeout=600.0, max_retries=1)

        system_prompt = (
            f"You are VALET, {USER_NAME}'s assistant. {USER_NAME} asked a research "
            "question. Use web_search and web_fetch to find real, current information — "
            "real product names, prices, addresses, source URLs. Never invent listings.\n\n"
            "LOCALE: the user is in the United States. Prices must be in USD ($). If "
            "a source quotes a non-USD price (£, €, ¥, etc.), either convert to a "
            "reasonable USD equivalent or OMIT the price field entirely — never "
            "display the foreign currency. Addresses and locations should likewise "
            "prefer US sources when the query has no geographic constraint.\n\n"
            "REQUIRED RESEARCH PROCEDURE — non-negotiable:\n"
            "1. Start with one or two web_search calls to identify candidate sources.\n"
            "2. Then web_fetch the 3-5 most relevant URLs from the search results. "
            "DO NOT synthesize your answer from search snippets alone — snippets are "
            "shallow and often missing the prices, specs, addresses, and metadata the "
            "user wants. The depth comes from fetching the actual pages.\n"
            "3. Only after fetching, write your final response.\n\n"
            "VALET's UI relies on web_fetch events to render source-preview cards "
            "(thumbnail + page title + snippet) and to extract product images from "
            "fetched pages. Skipping web_fetch leaves the UI empty of imagery and "
            "robs {user} of the visual context they expect. Always fetch.\n\n"
            "Your response renders as result cards in a visual panel plus a short "
            "spoken summary. You are NOT writing a file, document, or report. Do not "
            "say 'I will create a report' or 'see the attached document'. Reply in "
            "concise prose mentioning specific items the panel can extract as cards "
            "(products with prices, locations with addresses, web sources with URLs). "
            "Always include the source URL for each item you mention so cards can "
            "link back."
        ).replace("{user}", USER_NAME)

        messages: list[dict] = [{"role": "user", "content": target}]
        tools = [
            {"type": "web_search_20260209", "name": "web_search"},
            {"type": "web_fetch_20260209", "name": "web_fetch"},
        ]

        assistant_text_parts: list[str] = []
        tool_result_snippets: list[str] = []
        seen_search_ids: set[str] = set()
        seen_fetch_ids: set[str] = set()
        # FIFO queue of URLs parsed out of code_execution.input.code blocks.
        # With the _20260209 web_fetch tool version, the actual URL the
        # model is fetching lives in the Python source the model writes
        # inside a code_execution call (e.g. `for u in urls: await
        # web_fetch({"url": u})`); the standalone web_fetch tool_use blocks
        # that follow have input={}. So we extract URLs from each
        # code_execution block as it closes and pop the next URL when each
        # web_fetch block closes. Order-matched: the model executes the
        # web_fetch calls in the order URLs appear in the code.
        # See docs/streaming_hang_diagnosis.md → Finding A.
        pending_fetch_urls: list[str] = []
        # tool_use_id → URL — populated when a web_fetch server_tool_use
        # block closes; consumed when its matching web_fetch_tool_result
        # arrives so the source-preview card can be emitted with the right URL.
        fetch_url_by_id: dict[str, str] = {}
        searches = 0
        fetches = 0

        # Mid-research voice interjection — fires once if research hasn't
        # finished within 25s. Done-event signals an early return so the
        # interjection task can no-op cleanly.
        done_event = asyncio.Event()

        async def _maybe_interject() -> None:
            try:
                await asyncio.wait_for(done_event.wait(), timeout=25.0)
                # Finished before the timer — no interjection needed.
                return
            except asyncio.TimeoutError:
                pass
            if not ws:
                return
            try:
                msg = "Still gathering, sir."
                audio = await synthesize_speech(msg)
                if audio:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    await ws.send_json({
                        "type": "audio",
                        "data": base64.b64encode(audio).decode(),
                        "text": msg,
                    })
                    await ws.send_json({"type": "status", "state": "idle"})
                    log.info(f"VALET: {msg}")
            except Exception as e:
                log.debug(f"mid-research interjection failed: {e}")

        interjection_task = asyncio.create_task(_maybe_interject())

        async def _emit_progress() -> None:
            await emit_tool_event(
                task_id, "research.progress", "Research progress",
                detail=f"read {fetches} source{'s' if fetches != 1 else ''}, "
                       f"{searches} search{'es' if searches != 1 else ''}",
                status="active",
                payload={"fetched": fetches, "searched": searches},
            )

        await emit_step(task_id, "Searching the web…", status="active")

        try:
            # Server-side agent loop wrapped in streaming. Each turn opens a
            # stream context; we react to content_block events as they arrive
            # so panel events appear live rather than after the whole turn.
            # If a turn ends with stop_reason="pause_turn" we open another
            # stream to resume.
            #
            # Visibility layer (chunk 16): every stream event is logged at
            # INFO so a silent stall is visible in logs/valet.err.log.
            # A 60s watchdog (wait_for around iterator.__anext__) aborts
            # the stream loudly if no event arrives within that window —
            # SSE can silently disconnect, and without this the symptom
            # was "POST 200, then nothing for 6 minutes" (see
            # docs/streaming_hang_diagnosis.md).
            STREAM_IDLE_TIMEOUT_S = 60.0
            DELTA_HEARTBEAT_INTERVAL_S = 5.0
            for turn_idx in range(6):  # cap resumes to avoid runaway
                # Per-stream state: partial tool-use input JSON keyed by
                # content-block index (the SDK delivers input_json deltas
                # per index, then content_block_stop at index closure).
                pending_tool_use: dict[int, dict] = {}
                final_message = None
                log.info("stream_event turn=%d open (model=claude-opus-4-7, msgs=%d)",
                         turn_idx, len(messages))

                async with client.messages.stream(
                    model="claude-opus-4-7",
                    max_tokens=8192,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                ) as stream:
                    iterator = stream.__aiter__()
                    delta_count = 0
                    last_heartbeat = time.monotonic()
                    while True:
                        try:
                            event = await asyncio.wait_for(
                                iterator.__anext__(),
                                timeout=STREAM_IDLE_TIMEOUT_S,
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            log.error(
                                "stream_event WATCHDOG: silent for %.0fs — aborting "
                                "(turn=%d, deltas_so_far=%d, search=%d, fetch=%d)",
                                STREAM_IDLE_TIMEOUT_S, turn_idx, delta_count,
                                searches, fetches,
                            )
                            raise RuntimeError("stream_silent_timeout")

                        et = getattr(event, "type", None)

                        if et == "content_block_start":
                            block = getattr(event, "content_block", None)
                            btype = getattr(block, "type", None)
                            bname = getattr(block, "name", "") if btype == "server_tool_use" else ""
                            bid_log = getattr(block, "id", "") if btype == "server_tool_use" else ""
                            log.info(
                                "stream_event content_block_start index=%s type=%s name=%s id=%s",
                                event.index, btype, bname, bid_log,
                            )
                            if btype == "server_tool_use":
                                # Input streams via input_json_delta. Stash
                                # name+id and accumulate until block_stop.
                                pending_tool_use[event.index] = {
                                    "id": getattr(block, "id", "") or "",
                                    "name": getattr(block, "name", "") or "",
                                    "partial_json": "",
                                }
                            elif btype == "web_search_tool_result":
                                # Results arrive whole at content_block_start
                                # — collect snippets for middleware context.
                                results = getattr(block, "content", None)
                                result_count = len(results) if isinstance(results, list) else 0
                                log.info("stream_event web_search_tool_result results=%d", result_count)
                                if isinstance(results, list):
                                    for r in results:
                                        rtype = getattr(r, "type", None)
                                        if rtype == "web_search_result":
                                            url = getattr(r, "url", "") or ""
                                            title = getattr(r, "title", "") or ""
                                            if title or url:
                                                tool_result_snippets.append(f"{title}\n{url}")
                            elif btype == "web_fetch_tool_result":
                                rc = getattr(block, "content", None)
                                rc_count = len(rc) if isinstance(rc, list) else 0
                                tu_id = getattr(block, "tool_use_id", "") or ""
                                log.info("stream_event web_fetch_tool_result tool_use_id=%s parts=%d",
                                         tu_id, rc_count)
                                if isinstance(rc, list):
                                    for r in rc:
                                        txt = getattr(r, "text", None)
                                        if txt:
                                            tool_result_snippets.append(str(txt)[:2000])
                                # Emit a source-preview card for this URL.
                                # Spawned as a task so the (capped 1.5s)
                                # preview fetch doesn't slow the stream
                                # consumer.
                                fetched_url = fetch_url_by_id.get(tu_id, "")
                                if fetched_url:
                                    snippet = _extract_fetch_snippet(rc)
                                    asyncio.create_task(
                                        _emit_research_source_card(task_id, fetched_url, snippet)
                                    )

                        elif et == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            dtype = getattr(delta, "type", None)
                            delta_count += 1
                            now = time.monotonic()
                            if now - last_heartbeat >= DELTA_HEARTBEAT_INTERVAL_S:
                                log.info(
                                    "stream_event delta_heartbeat count=%d "
                                    "(rate=%.1f/s, last_type=%s)",
                                    delta_count, delta_count / max(0.1, now - last_heartbeat),
                                    dtype,
                                )
                                last_heartbeat = now
                            if dtype == "input_json_delta":
                                pending = pending_tool_use.get(event.index)
                                if pending is not None:
                                    pending["partial_json"] += getattr(delta, "partial_json", "") or ""
                            elif dtype == "text_delta":
                                txt = getattr(delta, "text", "") or ""
                                if txt:
                                    assistant_text_parts.append(txt)

                        elif et == "content_block_stop":
                            pending = pending_tool_use.pop(event.index, None)
                            if pending is None:
                                log.info("stream_event content_block_stop index=%s (non-tool)",
                                         event.index)
                                continue
                            try:
                                inp = json.loads(pending["partial_json"] or "{}")
                            except Exception:
                                inp = {}
                            bid = pending["id"]
                            name = pending["name"]
                            log.info(
                                "stream_event content_block_stop index=%s tool_use name=%s id=%s input=%r",
                                event.index, name, bid, inp,
                            )
                            if name == "code_execution":
                                # Dynamic filtering wraps web_fetch in
                                # Python that the model writes here. Pull
                                # URL literals out of the code and FIFO-
                                # queue them so the web_fetch blocks that
                                # follow can show real URLs in the panel
                                # and feed source-preview cards.
                                code = inp.get("code", "") if isinstance(inp, dict) else ""
                                extracted = _extract_urls_from_code(code)
                                if extracted:
                                    pending_fetch_urls.extend(extracted)
                                    log.info(
                                        "code_execution queued %d URL(s) (queue_depth=%d)",
                                        len(extracted), len(pending_fetch_urls),
                                    )
                            elif name == "web_search" and bid not in seen_search_ids:
                                seen_search_ids.add(bid)
                                searches += 1
                                query = inp.get("query", "") if isinstance(inp, dict) else ""
                                await emit_tool_event(
                                    task_id, "tool.web_search", "WebSearch",
                                    detail=query[:120],
                                    payload={"query": query},
                                )
                                await _emit_progress()
                            elif name == "web_fetch" and bid not in seen_fetch_ids:
                                seen_fetch_ids.add(bid)
                                fetches += 1
                                # _20260209 puts the URL inside the
                                # preceding code_execution block — input is
                                # empty here. Pop from the FIFO queue we
                                # built when those blocks closed. Fall
                                # back to whatever the SDK gave us so we
                                # don't break if Anthropic changes the
                                # shape and starts populating input again.
                                direct_url = inp.get("url", "") if isinstance(inp, dict) else ""
                                if direct_url:
                                    url = direct_url
                                elif pending_fetch_urls:
                                    url = pending_fetch_urls.pop(0)
                                    log.info(
                                        "web_fetch claimed queued URL (remaining=%d): %s",
                                        len(pending_fetch_urls), url[:120],
                                    )
                                else:
                                    url = ""
                                    log.warning(
                                        "web_fetch with no input and empty URL queue — "
                                        "panel row will be URL-less"
                                    )
                                if url:
                                    fetch_url_by_id[bid] = url
                                await emit_tool_event(
                                    task_id, "tool.web_fetch", "WebFetch",
                                    detail=url[:120],
                                    payload={"url": url},
                                )
                                await _emit_progress()

                        elif et == "message_stop":
                            log.info("stream_event message_stop")

                    final_message = await stream.get_final_message()
                    log.info(
                        "stream_event turn=%d closed stop_reason=%s in_tokens=%s out_tokens=%s",
                        turn_idx, getattr(final_message, "stop_reason", "?"),
                        getattr(final_message.usage, "input_tokens", "?"),
                        getattr(final_message.usage, "output_tokens", "?"),
                    )

                track_usage(final_message)
                messages.append({"role": "assistant", "content": final_message.content})

                if final_message.stop_reason == "pause_turn":
                    # API hit its server-side iteration limit; resume by
                    # opening another stream. No extra user message needed.
                    continue
                break

            response_text = "".join(assistant_text_parts).strip()
            # Signal the mid-research interjection task to cancel; emit a
            # final progress update so the panel chip reads the final count.
            done_event.set()
            await _emit_progress()
            await emit_step(
                task_id,
                f"Research complete — {searches} search(es), {fetches} fetch(es)",
                detail=f"{len(response_text)} chars",
                status="done",
            )

            # Card extraction — re-use the existing middleware unmodified.
            if response_text and anthropic_client:
                try:
                    import claude_middleware
                    asyncio.create_task(
                        claude_middleware.extract_and_emit(
                            response_text=response_text,
                            tool_result_snippets=tool_result_snippets,
                            task_id=task_id,
                            anthropic_client=anthropic_client,
                        )
                    )
                except Exception as e:
                    log.warning(f"native research middleware spawn failed: {e}")

            # Short voice summary via Haiku.
            if ws and response_text:
                try:
                    summary = await anthropic_client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=80,
                        system=(
                            "You are VALET. In ONE sentence, British butler tone, "
                            "first person, summarize the research finding for voice. "
                            "No markdown. End with 'sir' when natural."
                        ),
                        messages=[{"role": "user", "content": response_text[:2000]}],
                    )
                    msg = summary.content[0].text
                    audio = await synthesize_speech(msg)
                    if audio:
                        await ws.send_json({"type": "status", "state": "speaking"})
                        await ws.send_json({
                            "type": "audio",
                            "data": base64.b64encode(audio).decode(),
                            "text": msg,
                        })
                        await ws.send_json({"type": "status", "state": "idle"})
                        log.info(f"VALET: {msg}")
                except Exception as e:
                    log.warning(f"research voice summary failed: {e}")
        except Exception as e:
            log.error(f"Native research failed: {e}", exc_info=True)
            await emit_error(task_id, "Research failed", detail=str(e)[:200])
        finally:
            # Always signal so the interjection task exits even on error.
            done_event.set()
            if not interjection_task.done():
                interjection_task.cancel()


# Smart greeting — track last greeting to avoid re-greeting on reconnect
_last_greeting_time: float = 0


# ---------------------------------------------------------------------------
# TTS (Fish Audio)
# ---------------------------------------------------------------------------

async def synthesize_speech(text: str) -> Optional[bytes]:
    """Generate speech audio. Routes through the proxy's TTS endpoint when
    licensed (Fish Audio upstream); falls back to direct Fish in dev only."""
    voice_id = _active_voice_id()
    speed = _voice_speed()
    if LICENSE_KEY:
        url = f"{PROXY_BASE_URL}/api/proxy/tts"
        headers = {"X-License-Key": LICENSE_KEY, "Content-Type": "application/json"}
        payload = {"text": text, "reference_id": voice_id, "format": "mp3", "speed": speed}
    elif FISH_API_KEY:
        url = FISH_API_URL
        headers = {"Authorization": f"Bearer {FISH_API_KEY}", "Content-Type": "application/json"}
        payload = {"text": text, "reference_id": voice_id, "format": "mp3", "prosody": {"speed": speed}}
    else:
        log.warning("No LICENSE_KEY (or dev FISH_API_KEY) set, skipping TTS")
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                _session_tokens["tts_calls"] += 1
                _append_usage_entry(0, 0, "tts")
                return response.content
            else:
                log.error(f"TTS error: {response.status_code}")
                return None
    except Exception as e:
        log.error(f"TTS error: {e}")
        return None


# ---------------------------------------------------------------------------
# LLM Response
# ---------------------------------------------------------------------------

@observability.observe(name="generate-response", capture_input=False, capture_output=True)
async def generate_response(
    text: str,
    client: anthropic.AsyncAnthropic,
    task_mgr: ClaudeTaskManager,
    projects: list[dict],
    conversation_history: list[dict],
    last_response: str = "",
    session_summary: str = "",
) -> str:
    """Generate a VALET response using Anthropic API."""
    # License gate: refuse to assist when the license isn't entitled (with a
    # 7-day offline grace window). No-op in dev fallback (no LICENSE_KEY).
    await _ensure_license()
    _blocked = assistant_blocked_message()
    if _blocked:
        return _blocked

    now = datetime.now()
    current_time = now.strftime("%A, %B %d, %Y at %I:%M %p")

    # Use cached weather. Background thread + on-demand lookup both write a
    # dict {summary, raw, location}; older code paths used to write a plain
    # string. Accept either shape, the LLM only wants one line.
    _wx_cached = _ctx_cache.get("weather", "Weather data unavailable.")
    if isinstance(_wx_cached, dict):
        weather_info = _wx_cached.get("summary") or "Weather data unavailable."
    else:
        weather_info = _wx_cached

    # Use cached context (refreshed in background, never blocks responses)
    screen_ctx = _ctx_cache["screen"]
    calendar_ctx = _ctx_cache["calendar"]
    mail_ctx = _ctx_cache["mail"]

    # Check if any lookups are in progress
    lookup_status = get_lookup_status()

    # Build the personal-context block from .env-stored identity fields.
    # Reads .env fresh so edits in the settings panel take effect on the next
    # request without restarting the backend.
    _, _env_dict = _read_env()
    _dob = _env_dict.get("DATE_OF_BIRTH", "").strip()
    _addr = _env_dict.get("ADDRESS", "").strip()
    _work_email = _env_dict.get("WORK_EMAIL", "").strip() or WORK_EMAIL
    _personal_email = _env_dict.get("PERSONAL_EMAIL", "").strip() or PERSONAL_EMAIL
    _personal_lines = []
    if _dob: _personal_lines.append(f"- Date of birth: {_dob}")
    if _addr: _personal_lines.append(f"- Lives at: {_addr}")
    if _work_email:
        _personal_lines.append(
            f"- Work email: {_work_email} — for [ACTION:BROWSE], use "
            f"https://mail.google.com/mail/?authuser={_work_email}"
        )
    if _personal_email:
        _personal_lines.append(
            f"- Personal email: {_personal_email} — for [ACTION:BROWSE], use "
            f"https://mail.google.com/mail/?authuser={_personal_email}"
        )
    if _work_email and _personal_email:
        _personal_lines.append(
            '- When user says "my email" with no qualifier, default to the work email. '
            'When they say "work email" use the work URL above; "personal email" or '
            '"my Gmail" → personal URL above. Always use [ACTION:BROWSE] with the exact URL.'
        )
    personal_context = ("\nPERSONAL CONTEXT:\n" + "\n".join(_personal_lines) + "\n") if _personal_lines else ""

    # Static block — identity, behavior rules, full action descriptions. This
    # only changes when USER_NAME/PROJECT_DIR/personal .env values change, so
    # Anthropic's prompt cache hits it on virtually every request.
    static_system = VALET_SYSTEM_PROMPT.format(
        user_name=USER_NAME,
        project_dir=PROJECT_DIR,
        personal_context=personal_context,
    )

    # Dynamic block — live context that varies request-to-request. Not cached.
    dynamic_system = VALET_DYNAMIC_CONTEXT.format(
        current_time=current_time,
        weather_info=weather_info,
        screen_context=screen_ctx or "Not checked yet.",
        calendar_context=calendar_ctx,
        mail_context=mail_ctx,
        contacts_context=_format_contacts_for_prompt(),
        active_tasks=task_mgr.get_active_tasks_summary(),
        dispatch_context=dispatch_registry.format_for_prompt(),
        known_projects=format_projects_for_prompt(projects),
    )
    if lookup_status:
        dynamic_system += f"\n\nACTIVE LOOKUPS:\n{lookup_status}\nIf asked about progress, report this status."

    # Inject relevant memories and tasks
    memory_ctx = build_memory_context(text)
    if memory_ctx:
        dynamic_system += f"\n\nVALET MEMORY:\n{memory_ctx}"

    # Three-tier memory — inject rolling summary of earlier conversation
    if session_summary:
        dynamic_system += f"\n\nSESSION CONTEXT (earlier in this conversation):\n{session_summary}"

    # Self-awareness — remind VALET of last response to avoid repetition
    if last_response:
        dynamic_system += f'\n\nYOUR LAST RESPONSE (do not repeat this):\n"{last_response[:150]}"'

    # Runtime self-introspection — live wake phrases, agent registry, etc.
    # Lets VALET answer "what are your wake phrases?" without hallucinating
    # (we used to say "only valet, sir" because the LLM had no idea).
    try:
        import self_knowledge
        dynamic_system += "\n\n" + self_knowledge.get_self_knowledge_block()
    except Exception as _e:
        log.debug(f"self_knowledge injection failed: {_e}")

    # Use conversation history — keep the last 20 messages for context
    # (older conversation is captured in session_summary)
    messages = conversation_history[-20:]
    # If the last message isn't the current user text, add it
    if not messages or messages[-1].get("content") != text:
        messages = messages + [{"role": "user", "content": text}]

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=250,  # Extra room for [ACTION:X] tags
            system=[
                # Static block — cached for 5 minutes, ~6-8KB. Skips the
                # Anthropic API re-encoding it on every turn → much faster TTFT.
                {"type": "text", "text": static_system, "cache_control": {"type": "ephemeral"}},
                # Dynamic block — small live context that varies per request.
                {"type": "text", "text": dynamic_system},
            ],
            messages=messages,
        )
        track_usage(response)
        return response.content[0].text
    except Exception as e:
        log.error(f"LLM error: {e}")
        return "Apologies, sir. I'm having trouble connecting to my language systems."


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

# Shared state
task_manager = ClaudeTaskManager(max_concurrent=3)
anthropic_client: Optional[anthropic.AsyncAnthropic] = None
cached_projects: list[dict] = []
recently_built: list[dict] = []  # [{"name": str, "path": str, "time": float}]
dispatch_registry = DispatchRegistry()

# Stage D: the safety-gated control executor. Destructive (Tier 1) actions route
# through this — it asks the user to confirm and honors the global kill switch.
_base_executor = AppleScriptExecutor()
# Phase K / UC1: always layer the Accessibility backend underneath the
# AppleScript primary. It adds the universal-control primitives (observe_ui /
# click_element / key_combo) and a synthetic-input keystroke fallback for
# non-scriptable apps. The backend is import-safe and inert without pyobjc + the
# Accessibility grant (every method returns not_supported / a clean failure), so
# composing it unconditionally is safe on any host and never changes the
# AppleScript path for capabilities AppleScript already handles.
from composite_executor import CompositeExecutor
from accessibility_executor import AccessibilityExecutor
_ax_executor = AccessibilityExecutor()
_base_executor = CompositeExecutor(_base_executor, _ax_executor)
executor = SafeExecutor(_base_executor)
log.info("control backend: %s", executor.name)


async def _run_gated_action(ws: "WebSocket", result_coro) -> None:
    """Await a SafeExecutor capability (which may prompt for confirmation) and
    speak its outcome back over the WebSocket. Used for Tier 1 voice actions."""
    try:
        result = await result_coro
    except Exception as e:
        log.error(f"gated action error: {e}")
        return
    text = result.message or ("Done, sir." if result.ok else "I'm afraid that didn't go through, sir.")
    try:
        await ws.send_json({"type": "status", "state": "speaking"})
        audio = await synthesize_speech(strip_markdown_for_tts(text))
        if audio:
            await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": text})
        else:
            await ws.send_json({"type": "text", "text": text})
    except Exception:
        pass


# Tier 1 actions that don't route through the gated SafeExecutor (they have
# their own orchestration) but still need an explicit confirmation: calendar
# create/modify and sending keystrokes (outbound). Delete/move/write/applescript
# already confirm via the executor.
_CONFIRM_ACTIONS = {"create_event", "cancel_event", "send"}


async def _confirm_action(ws: "WebSocket", ea: dict) -> bool:
    """Ask the user to confirm a Tier 1 action. Returns True to allow. Speaks the
    cancellation on deny."""
    target = (ea.get("target") or "")[:140]
    summaries = {
        "create_event": f"Create a calendar event: {target}",
        "cancel_event": f"Cancel a calendar event: {target}",
        "send": f"Type and send into an app: {target}",
    }
    allowed = await confirmations.request(
        summary=summaries.get(ea["action"], f"Run {ea['action']}?"),
        targets=[target] if target else [],
        tier=1,
    )
    if not allowed:
        await _speak(ws, "Cancelled, sir.")
    return allowed


async def _confirm_and_dispatch(ws: "WebSocket", ea: dict) -> None:
    """Confirm a Tier 1 action, then run it — all on a background task.

    CRITICAL: this MUST NOT be awaited inline inside the WebSocket receive loop.
    `_confirm_action` blocks on the user's reply, which arrives as a
    `confirm_response` frame the receive loop has to read. Awaiting it inline
    parks the loop before its next `receive_text()`, so the reply is never
    consumed and the confirmation can only resolve at its 120s timeout (the
    "thinking forever" hang). Spawned as a task, the loop stays free to read the
    reply and the future resolves immediately on Allow/Deny.
    """
    if not await _confirm_action(ws, ea):
        return  # denied — _confirm_action already spoke
    action = ea["action"]
    target = ea.get("target", "")
    if action == "create_event":
        await _execute_create_event(target, ws)
    elif action == "cancel_event":
        await _execute_cancel_event(target, ws)
    elif action == "send":
        await _execute_type(target, press_enter=True)


# ── Account sync: wire SuccessTracker into the live loop + push a snapshot up ──
# The desktop app records task outcomes and action usage locally (SuccessTracker,
# tracking.py); a background loop periodically pushes a privacy-preserving
# aggregate snapshot to the proxy so the user's account page can show their
# profile, speech stats and connected apps. Telemetry-gated; no raw prompts or
# message content ever leave the machine — only counts, rates and the profile the
# user entered in onboarding.

success_tracker = None  # SuccessTracker | None — initialised in lifespan

# Integrations seen working this run. A side-effect-free signal for "connected":
# set true when a lookup of that type succeeds (no extra permission probes).
_connections_seen = {"calendar": False, "mail": False, "notes": False}


def _init_success_tracker():
    """Open the local SuccessTracker DB in the writable data dir. Never fatal."""
    global success_tracker
    try:
        from tracking import SuccessTracker
        success_tracker = SuccessTracker(
            db_path=str(valet_data_dir() / "valet_data.db")
        )
        log.info("success tracker ready")
    except Exception as e:
        success_tracker = None
        log.warning(f"success tracker unavailable: {e}")


def _track_usage(action_type: str):
    """Record one dispatched action for the top-requests breakdown. No-op safe."""
    if not success_tracker or not action_type:
        return
    try:
        success_tracker.log_usage(action_type)
    except Exception:
        pass


def _track_task(task_type: str, success: bool, duration: float = 0.0):
    """Record a completed task's outcome + duration. No-op safe."""
    if not success_tracker:
        return
    try:
        success_tracker.log_task(task_type, "", success, duration=duration)
    except Exception:
        pass


def _telemetry_on() -> bool:
    return os.getenv("VALET_TELEMETRY", "on").strip().lower() not in (
        "0", "off", "false", "no",
    )


def _app_version() -> str:
    try:
        p = Path(__file__).resolve().parent / "build_id.txt"
        if p.exists():
            return p.read_text().strip()[:40]
    except Exception:
        pass
    return "0.1.0"


def _gather_sync_snapshot() -> dict:
    """Build the snapshot for /api/proxy/sync: the onboarding profile + local
    aggregate stats + connection flags. No message content."""
    profile = {}
    for key, env in (
        ("name", "USER_NAME"),
        ("honorific", "HONORIFIC"),
        ("date_of_birth", "DATE_OF_BIRTH"),
        ("location", "HOMETOWN_CITY"),
        ("work_email", "WORK_EMAIL"),
        ("personal_email", "PERSONAL_EMAIL"),
    ):
        v = (os.getenv(env, "") or "").strip()
        # USER_NAME defaults to the placeholder "sir"; don't sync that as a name.
        if v and not (env == "USER_NAME" and v.lower() == "sir"):
            profile[key] = v
    if not profile.get("location"):
        addr = (os.getenv("ADDRESS", "") or "").strip()
        if addr:
            profile["location"] = addr

    stats = {}
    if success_tracker:
        try:
            rate = success_tracker.get_success_rate()
            stats["total_tasks"] = rate.get("total", 0)
            stats["success_rate"] = round(rate.get("rate", 0.0), 1)
            stats["avg_duration_seconds"] = round(
                success_tracker.get_avg_duration(), 2
            )
            stats["top_actions"] = [
                {"action": t["action_type"], "count": t["count"]}
                for t in success_tracker.get_top_actions(limit=8)
            ]
        except Exception as e:
            log.warning(f"sync stats gather failed: {e}")

    return {
        "profile": profile or None,
        "stats": stats or None,
        "connections": dict(_connections_seen),
        "app_version": _app_version(),
    }


async def _push_account_sync():
    """POST one snapshot to the proxy. Best-effort; failures logged, not fatal."""
    if not LICENSE_KEY or not _telemetry_on():
        return
    try:
        payload = _gather_sync_snapshot()
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(
                f"{PROXY_BASE_URL}/api/proxy/sync",
                headers={"X-License-Key": LICENSE_KEY},
                json=payload,
            )
        if r.status_code >= 300:
            log.warning(f"account sync failed: {r.status_code}")
    except Exception as e:
        log.warning(f"account sync error: {e}")


async def _account_sync_loop():
    """Push a snapshot shortly after start, then every 15 minutes."""
    try:
        await asyncio.sleep(20)  # let startup settle first
        while True:
            await _push_account_sync()
            await asyncio.sleep(900)
    except asyncio.CancelledError:
        pass


async def _fetch_and_apply_device_settings():
    """Pull the user's web-controlled settings (set on the account dashboard) and
    apply them locally: voice, the advanced voice-id override, and telemetry.

    The web is the source of truth AT LAUNCH; in-app toggles still override for
    the running session. Best-effort — never fatal, never blocks startup.
    """
    if not LICENSE_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(
                f"{PROXY_BASE_URL}/api/proxy/device-settings",
                headers={"X-License-Key": LICENSE_KEY},
            )
        if r.status_code >= 300:
            return
        settings = (r.json() or {}).get("settings") or {}
    except Exception as e:
        log.warning(f"device-settings fetch failed: {e}")
        return
    try:
        voice = settings.get("voice")
        if voice in ("male", "female"):
            _write_env_key("VALET_VOICE", voice)
        voice_id = (settings.get("voice_id") or "").strip()
        if voice_id:
            # The advanced override maps to the male reference id, which
            # _active_voice_id() reads live via VALET_VOICE_MALE_ID.
            _write_env_key("VALET_VOICE_MALE_ID", voice_id)
            _write_env_key("FISH_VOICE_ID", voice_id)
        tel = settings.get("telemetry")
        if isinstance(tel, bool):
            _write_env_key("VALET_TELEMETRY", "on" if tel else "off")
        log.info("applied web device-settings")
    except Exception as e:
        log.warning(f"device-settings apply failed: {e}")


# Usage tracking — logs every call with timestamp, persists to disk
_USAGE_FILE = Path(__file__).parent / "data" / "usage_log.jsonl"
_session_start = time.time()
_session_tokens = {"input": 0, "output": 0, "api_calls": 0, "tts_calls": 0}


def _append_usage_entry(input_tokens: int, output_tokens: int, call_type: str = "api"):
    """Append a usage entry with timestamp to the log file."""
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        entry = {
            "ts": time.time(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "type": call_type,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with open(_USAGE_FILE, "a") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass


def _get_usage_for_period(seconds: float | None = None) -> dict:
    """Sum usage from the log file for a time period. None = all time."""
    import json as _json
    totals = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0, "tts_calls": 0}
    cutoff = (time.time() - seconds) if seconds else 0
    try:
        if _USAGE_FILE.exists():
            for line in _USAGE_FILE.read_text().strip().split("\n"):
                if not line:
                    continue
                entry = _json.loads(line)
                if entry["ts"] >= cutoff:
                    totals["input_tokens"] += entry.get("input_tokens", 0)
                    totals["output_tokens"] += entry.get("output_tokens", 0)
                    if entry.get("type") == "tts":
                        totals["tts_calls"] += 1
                    else:
                        totals["api_calls"] += 1
    except Exception:
        pass
    return totals


def _cost_from_tokens(input_t: int, output_t: int) -> float:
    return (input_t / 1_000_000) * 0.80 + (output_t / 1_000_000) * 4.00


def track_usage(response):
    """Track token usage from an Anthropic API response."""
    inp = getattr(response.usage, "input_tokens", 0) if hasattr(response, "usage") else 0
    out = getattr(response.usage, "output_tokens", 0) if hasattr(response, "usage") else 0
    _session_tokens["input"] += inp
    _session_tokens["output"] += out
    _session_tokens["api_calls"] += 1
    _append_usage_entry(inp, out, "api")


def get_usage_summary() -> str:
    """Get a voice-friendly usage summary with time breakdowns."""
    uptime_min = int((time.time() - _session_start) / 60)

    session = _session_tokens
    today = _get_usage_for_period(86400)
    week = _get_usage_for_period(86400 * 7)
    all_time = _get_usage_for_period(None)

    session_cost = _cost_from_tokens(session["input"], session["output"])
    today_cost = _cost_from_tokens(today["input_tokens"], today["output_tokens"])
    all_cost = _cost_from_tokens(all_time["input_tokens"], all_time["output_tokens"])

    parts = [f"This session: {uptime_min} minutes, {session['api_calls']} calls, ${session_cost:.2f}."]

    if today["api_calls"] > session["api_calls"]:
        parts.append(f"Today total: {today['api_calls']} calls, ${today_cost:.2f}.")

    if all_time["api_calls"] > today["api_calls"]:
        parts.append(f"All time: {all_time['api_calls']} calls, ${all_cost:.2f}.")

    return " ".join(parts)

# Background context cache — never blocks responses
_ctx_cache = {
    "screen": "",
    "calendar": "No calendar data yet.",
    "mail": "No mail data yet.",
    "weather": "Weather data unavailable.",
}


def _refresh_context_sync():
    """Run in a SEPARATE THREAD — refreshes screen/calendar/mail context.

    This runs completely off the async event loop so it never blocks responses.
    """
    import threading

    def _worker():
        while True:
            try:
                # Screen — fast
                try:
                    proc = __import__("subprocess").run(
                        ["osascript", "-e", '''
set windowList to ""
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    set visibleApps to every application process whose visible is true
    repeat with proc in visibleApps
        set appName to name of proc
        try
            set winCount to count of windows of proc
            if winCount > 0 then
                repeat with w in (windows of proc)
                    try
                        set winTitle to name of w
                        if winTitle is not "" and winTitle is not missing value then
                            set windowList to windowList & appName & "|||" & winTitle & "|||" & (appName = frontApp) & linefeed
                        end if
                    end try
                end repeat
            end if
        end try
    end repeat
end tell
return windowList
'''],
                        capture_output=True, text=True, timeout=5
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        windows = []
                        for line in proc.stdout.strip().split("\n"):
                            parts = line.strip().split("|||")
                            if len(parts) >= 3:
                                windows.append({
                                    "app": parts[0].strip(),
                                    "title": parts[1].strip(),
                                    "frontmost": parts[2].strip().lower() == "true",
                                })
                        if windows:
                            _ctx_cache["screen"] = format_windows_for_context(windows)
                except Exception:
                    pass

            except Exception as e:
                log.debug(f"Context thread error: {e}")

            # Weather — refresh every loop. Uses the user's HOMETOWN_CITY
            # (with ADDRESS as fallback). Geocode result is cached on
            # _ctx_cache["_weather_geo"] so we don't re-hit the geocoder
            # every 30s. The rich shape stays alongside a single-line summary
            # for the dynamic system prompt; both are picked up downstream.
            try:
                import asyncio as _wx_aio
                import weather as _wx
                _, _wx_env = _read_env()
                _wx_city = (_wx_env.get("HOMETOWN_CITY") or "").strip()
                if not _wx_city:
                    _wx_city = (_wx_env.get("ADDRESS") or "").strip()
                if _wx_city:
                    _wx_geo = _ctx_cache.get("_weather_geo") or None
                    if not _wx_geo or _wx_geo.get("query") != _wx_city:
                        _wx_geo = _wx_aio.run(_wx.geocode(_wx_city))
                        if _wx_geo:
                            _wx_geo["query"] = _wx_city
                            _ctx_cache["_weather_geo"] = _wx_geo
                    if _wx_geo:
                        _wx_fc = _wx_aio.run(_wx.fetch_forecast(
                            _wx_geo["lat"], _wx_geo["lon"], _wx_geo["timezone"],
                        ))
                        if _wx_fc:
                            _ctx_cache["weather"] = {
                                "summary": _wx.quick_summary_for_prompt(_wx_fc, _wx_geo["name"]),
                                "raw": _wx_fc,
                                "location": _wx_geo["name"],
                            }
            except Exception as _wx_e:
                log.debug(f"weather context refresh failed: {_wx_e}")

            # Calendar — refresh today's events so VALET always knows the schedule.
            # Without this, the system prompt forever reads "No calendar data yet"
            # and VALET tells the user calendar isn't connected.
            try:
                import asyncio as _aio
                events = _aio.run(get_todays_events())
                _ctx_cache["calendar"] = (
                    format_events_for_context(events)
                    if events else "No events scheduled today."
                )
            except Exception as e:
                log.debug(f"calendar context refresh failed: {e}")

            time.sleep(30)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    log.info("Context refresh thread started")


# ---------------------------------------------------------------------------
# License gate
# ---------------------------------------------------------------------------

_last_license_check = 0.0


def _license_status_label() -> str:
    """Settings-panel label for license state ('dev' when running keyless)."""
    if not LICENSE_KEY:
        return "dev"
    try:
        import licensing
        return licensing.status_label()
    except Exception:
        return "unknown"


async def _ensure_license() -> None:
    """Lazily revalidate the license at most every ~15 minutes."""
    global _last_license_check
    if not LICENSE_KEY:
        return
    import time as _t
    import licensing
    now = _t.time()
    if now - _last_license_check > 900:
        _last_license_check = now
        await licensing.validate(LICENSE_KEY, PROXY_BASE_URL)


def assistant_blocked_message() -> Optional[str]:
    """Butler line to speak instead of working when the license is not entitled.
    Returns None when entitled or in dev fallback (no LICENSE_KEY → no gate)."""
    if not LICENSE_KEY:
        return None
    import licensing
    if licensing.is_entitled():
        return None
    return (
        "Apologies, sir — my licence requires attention before I can assist. "
        "Do review your subscription."
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    global anthropic_client, cached_projects
    # Stage D: route Tier 1 confirmation prompts to connected frontends. The
    # confirm card listens for {"type":"confirm_request"} and replies with
    # {"type":"confirm_response"}.
    confirmations.set_sender(task_manager._notify)
    # Enable Langfuse tracing before any LLM call so every Anthropic request is
    # captured. No-op (and never raises) if Langfuse isn't configured.
    observability.setup_observability()
    # Stage F: opt-in error reporting — no-op unless the user consented
    # (VALET_TELEMETRY) and a SENTRY_DSN is set. Payloads are scrubbed.
    import sentry_setup
    sentry_setup.setup_telemetry()
    _start_parent_watchdog()
    # max_retries=1 (default is 2): on 429 we want to surface the error quickly
    # instead of waiting through two exponential-backoff retries (which caused
    # those 10-15s response stalls during heavy use).
    if LICENSE_KEY:
        # Route every model call through the proxy. base_url makes the SDK POST
        # to <proxy>/api/proxy/v1/messages; the proxy injects the real vendor key
        # (the placeholder api_key below is ignored) and meters usage per license.
        anthropic_client = anthropic.AsyncAnthropic(
            api_key="license-proxy",
            base_url=f"{PROXY_BASE_URL}/api/proxy",
            default_headers={"X-License-Key": LICENSE_KEY},
            max_retries=1,
            timeout=20.0,
        )
        log.info(f"LLM via proxy: {PROXY_BASE_URL}/api/proxy")
    elif ANTHROPIC_API_KEY:
        # Dev-only fallback (internal repo): direct vendor access, no license.
        anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=1, timeout=20.0)
        log.warning("LLM via direct ANTHROPIC_API_KEY (dev fallback — no proxy/license)")
    else:
        log.warning("No LICENSE_KEY or ANTHROPIC_API_KEY — LLM features disabled")

    # Validate the license up front. The assistant loop is gated on this (with a
    # 7-day offline grace window); dev fallback (no LICENSE_KEY) skips the gate.
    if LICENSE_KEY:
        import licensing
        state = await licensing.validate(LICENSE_KEY, PROXY_BASE_URL)
        if licensing.is_entitled():
            log.info(f"license OK: {licensing.status_label()}")
        else:
            log.warning(f"license NOT entitled: status={state.get('status')} — assistant loop disabled")

    # Wire the local success tracker, and start the account-sync loop when the
    # user is licensed and hasn't opted out of telemetry.
    _init_success_tracker()
    _sync_task = None
    if LICENSE_KEY and _telemetry_on():
        _sync_task = asyncio.create_task(_account_sync_loop())

    # Apply web-controlled device settings (voice / voice id / telemetry) set on
    # the account dashboard. Web wins at launch; in-app toggles override for the
    # session. Fired as a task so it never blocks startup.
    if LICENSE_KEY:
        asyncio.create_task(_fetch_and_apply_device_settings())

    cached_projects = []

    # Start context refresh in a separate thread (never touches event loop)
    _refresh_context_sync()

    # One-shot cleanup of project_aliases: repair moved projects (basename
    # match in any configured root) or delete truly orphaned rows. Silent
    # self-heal — prevents the alias table from accumulating cruft over time.
    try:
        from memory import cleanup_stale_aliases
        from actions import _project_roots
        cleanup_result = cleanup_stale_aliases([str(r) for r in _project_roots()])
        if cleanup_result["repaired"]:
            log.info(f"alias cleanup repaired: {cleanup_result['repaired']}")
        if cleanup_result["deleted"]:
            log.info(f"alias cleanup deleted: {cleanup_result['deleted']}")
    except Exception as e:
        log.warning(f"cleanup_stale_aliases failed: {e}")

    log.info("VALET server starting")

    yield

    if _sync_task:
        _sync_task.cancel()
    # Flush any buffered traces so nothing is lost on shutdown.
    observability.shutdown_observability()


app = FastAPI(title="VALET Server", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Process-panel screenshot store. ProcessEventBus emits screenshot events with
# a path relative to this dir; the frontend fetches the file from /screenshots/.
SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOTS_DIR)), name="screenshots")


# -- REST Endpoints --------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "online", "name": "VALET", "version": "0.1.0"}


@app.post("/api/_debug/emit-demo")
async def debug_emit_demo():
    """Fire one event of each type into an open-ended task. The panel stays
    visible until the user dismisses it ("close it" / X button) because
    no task_done is emitted.

    Dev-only — handy for smoke-testing the panel without spinning up a
    real claude -p run. Safe to leave in: requires POST and only emits
    process_event broadcasts (no side effects).
    """
    from process_events import Event, EventStatus
    task_id = uuid.uuid4().hex[:8]

    await process_bus.emit(Event(
        type="task_start",
        task_id=task_id,
        title="DEMO: process panel walkthrough",
        detail="Stays open until you say 'close it' or click ×",
        status=EventStatus.ACTIVE.value,
    ))
    await emit_step(task_id, "Phase one running", detail="this is a step event", status="active")
    await emit_browser_action(task_id, "Visit page", url="https://example.com/demo")
    await emit_app_launch(task_id, "Slack", status="done", detail="(simulated)")
    await emit_text_write(task_id, "Chrome", "the quick brown fox jumps over the lazy dog", sent=False)
    for line in (
        "$ claude -p --output-format text",
        "Analyzing project structure...",
        "Found 32 source files",
        "Drafting response...",
        "Done.",
    ):
        await emit_code_task(task_id, line)
    await emit_task_queued(task_id, "Next: deploy step", detail="queued for later")
    await emit_error(task_id, "Simulated error", detail="this is what an error event looks like")
    # Deliberately do NOT emit task_done so the panel stays open.
    return {"task_id": task_id, "status": "emitted; panel will stay open"}


@app.get("/api/tts-test")
async def tts_test():
    """Generate a test audio clip for debugging."""
    audio = await synthesize_speech("Testing audio, sir.")
    if audio:
        return {"audio": base64.b64encode(audio).decode()}
    return {"audio": None, "error": "TTS failed"}


@app.get("/api/usage")
async def api_usage():
    uptime = int(time.time() - _session_start)
    today = _get_usage_for_period(86400)
    week = _get_usage_for_period(86400 * 7)
    month = _get_usage_for_period(86400 * 30)
    all_time = _get_usage_for_period(None)
    return {
        "session": {**_session_tokens, "uptime_seconds": uptime},
        "today": {**today, "cost_usd": round(_cost_from_tokens(today["input_tokens"], today["output_tokens"]), 4)},
        "week": {**week, "cost_usd": round(_cost_from_tokens(week["input_tokens"], week["output_tokens"]), 4)},
        "month": {**month, "cost_usd": round(_cost_from_tokens(month["input_tokens"], month["output_tokens"]), 4)},
        "all_time": {**all_time, "cost_usd": round(_cost_from_tokens(all_time["input_tokens"], all_time["output_tokens"]), 4)},
    }


@app.get("/api/tasks")
async def api_list_tasks():
    tasks = await task_manager.list_tasks()
    return {"tasks": [t.to_dict() for t in tasks]}


@app.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str):
    task = await task_manager.get_status(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"error": "Task not found"})
    return {"task": task.to_dict()}


@app.post("/api/tasks")
async def api_create_task(req: TaskRequest):
    try:
        task_id = await task_manager.spawn(req.prompt, req.working_dir)
        return {"task_id": task_id, "status": "spawned"}
    except RuntimeError as e:
        return JSONResponse(status_code=429, content={"error": str(e)})


@app.delete("/api/tasks/{task_id}")
async def api_cancel_task(task_id: str):
    cancelled = await task_manager.cancel(task_id)
    if not cancelled:
        return JSONResponse(
            status_code=404,
            content={"error": "Task not found or not cancellable"},
        )
    return {"task_id": task_id, "status": "cancelled"}


@app.get("/api/agents")
async def api_list_agents():
    """All Claude Code sub-agents VALET can dispatch to.

    Sourced from <project>/.claude/agents/*.md, ~/.claude/agents/*.md, and
    a builtin list. The frontend's design-panel agent dropdown reads this;
    the voice "use the X agent" fast-path resolves names against it too.
    """
    import agents
    project_path = Path(__file__).parent
    return {"agents": agents.list_agents(project_path)}


@app.get("/api/projects")
async def api_list_projects():
    """All project directories under configured roots, plus alias entries.

    Unlike `scan_projects()` (used for LLM context), this does NOT filter
    out non-git directories — the design panel's target dropdown needs to
    surface every project folder, even unversioned scratch dirs, so the
    user can ship a prompt to anything in ~/Code.
    """
    projects = []
    for entry in list_projects():
        path = Path(entry["path"])
        if not path.is_dir():
            continue
        # Best-effort branch lookup; "" if not a git repo.
        branch = ""
        head_file = path / ".git" / "HEAD"
        try:
            if head_file.exists():
                head_content = head_file.read_text().strip()
                if head_content.startswith("ref: refs/heads/"):
                    branch = head_content.replace("ref: refs/heads/", "")
                else:
                    branch = head_content[:8]  # detached HEAD
        except Exception:
            pass
        projects.append({
            "name": entry["name"],
            "path": str(path),
            "branch": branch,
            "source": entry.get("source", "fs"),
        })
    return {"projects": projects}


# -- Fast Action Detection (no LLM call) -----------------------------------

def _scan_projects_sync() -> list[dict]:
    """Synchronous startup-time project scan — runs in executor.

    Pulls from `list_projects()` so the cached_projects context surfaces both
    alias-recorded projects and filesystem scans of ~/Code + ~/projects. No
    git-branch lookup here — that's left to the async `scan_projects()`.
    """
    try:
        return [{"name": p["name"], "path": p["path"], "branch": ""}
                for p in list_projects()]
    except Exception:
        return []


# Project-intent regexes. Structured patterns so we catch real speech
# (filler words, "in cursor" suffix, no-hyphen STT artifacts) rather than
# only matching a hand-picked substring list.
_OPEN_PROJECT_PATTERNS = [
    # "open the project called/named X" (more specific — tried first)
    _action_re.compile(
        r'^\s*(?:can you |could you |please )?'
        r'(?:open|launch|pull up|bring up|fire up)\s+'
        r'(?:the |a |my )?project\s+(?:called|named)\s+'
        r'(?P<name>[\w.\- ]+?)'
        r'\s*\??\.?\s*$',
        _action_re.IGNORECASE,
    ),
    # Standard: "[can you/please] open [the/my/that/a] NAME [project] [in cursor]"
    _action_re.compile(
        r'^\s*(?:can you |could you |please )?'
        r'(?:open|launch|pull up|bring up|fire up)\s+'
        r'(?:the |my |that |a )?'
        r'(?P<name>[\w.\- ]+?)'
        r'(?:\s+project)?'
        r'(?:\s+in\s+cursor)?'
        r'\s*\??\.?\s*$',
        _action_re.IGNORECASE,
    ),
]

# "open/launch my calendar|email|mail|notes|…" must LAUNCH the app, not route to
# the read-the-data lookups (check_calendar/check_mail). Maps the spoken name to
# the macOS app; checked BEFORE those lookups in detect_action_fast. ("check my
# calendar" / "what's on my calendar" don't start with a launch verb, so they
# still reach the lookups.)
_OPEN_APP_LAUNCH_RE = _action_re.compile(
    r'^\s*'
    # Tolerate a leaked wake word ("hey vee, open …") if it wasn't stripped.
    r'(?:(?:hey|ok|okay|yo)\s+)?(?:vee|v|valet)?[\s,]*'
    # Optional polite lead-ins, any number.
    r"(?:(?:can|could|would|will) you |please |i(?:'d like| want| wanna)(?: to)? |let'?s |go ahead and |go )*"
    # Launch verbs only — unambiguous "open the app". ("show me / what's on my
    # calendar" stay with the read lookups; they have no launch verb here.)
    r'(?:open(?:\s+up)?|launch|pull up|bring up|fire up)\s+'
    r'(?:the |my |up |to )?'
    r'(calendar|mail|e-?mail|inbox|notes|reminders|messages|music|photos|maps|contacts|facetime|safari|finder)'
    r'(?:\s+app)?'
    r'(?:\s+(?:for me|now|please|up))?'
    r'\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)
_OPEN_APP_LAUNCH_MAP = {
    "calendar": "Calendar", "mail": "Mail", "email": "Mail", "e-mail": "Mail",
    "inbox": "Mail", "notes": "Notes", "reminders": "Reminders",
    "messages": "Messages", "music": "Music", "photos": "Photos", "maps": "Maps",
    "contacts": "Contacts", "facetime": "FaceTime", "safari": "Safari",
    "finder": "Finder",
}

# Web destinations openable by name — "open my gmail", "open google analytics in
# chrome", "go to github". Routed to open_url (open -a <browser> <url>), which
# needs NO keystrokes / Accessibility (the old path tried to drive Chrome via
# System Events and failed with -1728/1002). Native-app launches above are
# matched first, so "open my calendar" still launches Calendar.app. Entries are
# multi-word or unambiguous brands so a bare project name ("open analytics") is
# NOT hijacked — single ambiguous words are deliberately left out.
_WEB_DESTINATIONS = {
    "gmail": "https://mail.google.com",
    "google mail": "https://mail.google.com",
    "google calendar": "https://calendar.google.com",
    "google analytics": "https://analytics.google.com",
    "google drive": "https://drive.google.com",
    "google docs": "https://docs.google.com",
    "google sheets": "https://sheets.google.com",
    "google slides": "https://slides.google.com",
    "google meet": "https://meet.google.com",
    "google maps": "https://maps.google.com",
    "google search": "https://www.google.com",
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gemini": "https://gemini.google.com",
    "github": "https://github.com",
    "stripe": "https://dashboard.stripe.com",
    "vercel": "https://vercel.com/dashboard",
    "supabase": "https://supabase.com/dashboard",
    "notion": "https://www.notion.so",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "amazon": "https://www.amazon.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
}

# Spoken browser name → open_browser() arg (it knows chrome/firefox; safari maps
# to chrome's loader path which `open -a` resolves by display name anyway).
_OPEN_BROWSER_MAP = {
    "chrome": "chrome", "google chrome": "chrome", "firefox": "firefox",
    "safari": "safari", "the browser": "chrome", "a browser": "chrome",
    "my browser": "chrome", "browser": "chrome",
}

_OPEN_WEB_RE = _action_re.compile(
    r'^\s*'
    r'(?:(?:hey|ok|okay|yo)\s+)?(?:vee|v|valet)?[\s,]*'
    r"(?:(?:can|could|would|will) you |please |i(?:'d like| want| wanna)(?: to)? |let'?s |go ahead and )*"
    r'(?:open(?:\s+up)?|launch|pull up|bring up|fire up|go to|navigate to|take me to)\s+'
    r'(?:the |my )?'
    r'(?P<target>.+?)'
    r'(?:\s+(?:in|on|with|using)\s+(?P<browser>google chrome|chrome|firefox|safari|the browser|a browser|my browser|browser))?'
    r'\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)


def _resolve_web_url(name: str) -> str | None:
    """Spoken destination → URL. Known service, bare domain (github.com), or None
    (caller decides whether to fall back to a search)."""
    n = name.strip().lower().rstrip(".?! ")
    if n in _WEB_DESTINATIONS:
        return _WEB_DESTINATIONS[n]
    if _action_re.match(r'^[a-z0-9.\-]+\.[a-z]{2,}(?:/\S*)?$', n):
        return n if n.startswith("http") else f"https://{n}"
    return None


# "go to / open / pull up my <title> note" → SHOW that note in Notes.app (vs.
# read_note, which reads it aloud). Requires a title before "note", so "open my
# notes" (plural) still hits the Notes.app launcher above.
_OPEN_NOTE_RE = _action_re.compile(
    r'^\s*'
    r'(?:(?:hey|ok|okay|yo)\s+)?(?:vee|v|valet)?[\s,]*'
    r"(?:(?:can|could|would|will) you |please |i(?:'d like| want| wanna)(?: to)? |let'?s |go ahead and )*"
    r'(?:open(?:\s+up)?|go to|pull up|bring up|take me to|show me|jump to|find)\s+'
    r'(?:the |my )?'
    r'(?P<title>.+?)'
    r'\s+note\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)

# Register-project intent — for one-off projects outside any configured root.
# All patterns capture an absolute or tilde-prefixed path; alias is optional
# (defaults to the dir's basename in register_project).
_REGISTER_PROJECT_PATTERNS = [
    # "register <path> as [my/the/a] <alias> [project]" / same with "add"
    _action_re.compile(
        r'^\s*(?:register|add)\s+(?P<path>[~/][^\s]+)\s+as\s+'
        r'(?:my\s+|the\s+|a\s+)?(?P<alias>[\w.\- ]+?)'
        r'(?:\s+project)?\.?\s*$',
        _action_re.IGNORECASE,
    ),
    # "remember <path> as [my/the/a] <alias> [project]"
    _action_re.compile(
        r'^\s*remember\s+(?P<path>[~/][^\s]+)\s+as\s+'
        r'(?:my\s+|the\s+|a\s+)?(?P<alias>[\w.\- ]+?)'
        r'(?:\s+project)?\.?\s*$',
        _action_re.IGNORECASE,
    ),
    # "add (this) project: <path>" — alias falls back to basename
    _action_re.compile(
        r'^\s*add\s+(?:this\s+)?project[:\s]+(?P<path>[~/][^\s]+)\s*\.?\s*$',
        _action_re.IGNORECASE,
    ),
]

# Dispatch-to-agent intent — "use the explore agent to find X" / "ask the
# plan agent to design Y" / "have the debugger agent look at Z". Captures
# the agent name (loose — STT may flatten hyphens to spaces) and the task.
# The action handler normalizes the name and matches against the live
# /api/agents result, so the user can say "general purpose agent" or
# "general-purpose agent" interchangeably.
_AGENT_DISPATCH_PATTERN = _action_re.compile(
    r'^\s*(?:use|ask|have|tell)\s+the\s+'
    r'(?P<agent>[\w\- ]+?)\s+(?:sub[-\s]?)?agent\s+'
    r'(?:to\s+|for\s+|on\s+)?'
    r'(?P<task>.+?)\s*\.?\s*$',
    _action_re.IGNORECASE,
)

# Greenfield design intent — "new project for X" / "design a new project X" /
# "spin up a new project called X" / "start a new project for X". Captures
# the project name so the session is created with new_project_name already
# attached and the design partner skips straight to the stack question.
_NEW_PROJECT_DESIGN_PATTERN = _action_re.compile(
    r'^\s*(?:let\'?s |can we |please |i (?:want to|wanna|wish to) )?'
    r'(?:design |spin up |start |create |build )(?:a )?new project '
    r'(?:for |called |named )?'
    r'(?P<name>[\w .,\'\-]+?)\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)

# Start-design intent — "let's design X" / "design a Y" / "spec X" / "plan a Z".
# Captures the topic so we can spawn a session with it immediately.
#
# Lead-in group is deliberately generous: people open the design panel with all
# sorts of polite preambles ("I'd like to…", "could we…", "time to…"). The verb
# set is the design-intent core; "spec out"/"scope out"/etc. are listed before
# their bare forms so the trailing "out" is consumed as part of the verb, not
# captured into the topic.
_START_DESIGN_PATTERN = _action_re.compile(
    r'^\s*(?:'
    r"let'?s |let us |let me |"
    r"i'?d (?:like|love) to |i would (?:like|love) to |"
    r'i (?:want to|wanna|wish to|need to) |'
    r'(?:want to|wanna|need to) |'
    r'can we |could we |shall we |should we |we should |'
    r'how about (?:we )?|what if we |time to |please '
    r')?'
    r'(?:design|spec out|spec|architect|plan|think through|'
    r'brainstorm|prototype|scope out|scope|flesh out|sketch out|sketch|map out)\s+'
    r'(?:a |an |the |some )?(?P<topic>[\w .,\'\-]+?)\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)

# Explicit design-mode opt-in phrases. Substring match, case-insensitive.
# Matched AFTER _START_DESIGN_PATTERN so an utterance with a topic ("let's
# design a recipe tracker") still captures the topic. These phrases trigger
# START_DESIGN with no target — Valet prompts for the topic next turn.
# See docs/design_handoff_diagnosis.md — Option C.
_DESIGN_OPTIN_PHRASES = (
    "design mode",
    "talk about a feature",
    "discuss a change",
    "let's design",
    "lets design",
    "brainstorm a feature",
    "plan a feature",
    "think about adding",
    "design before we build",
    # More natural openers for the design panel. Topic-less by design — these
    # trigger START_DESIGN with no target, and VALET asks for the topic next
    # turn (the "what should this feature do?" prompt).
    "design a feature",
    "design a new feature",
    "design something",
    "design a new",
    "spec out a feature",
    "plan a new feature",
    "work on a new feature",
    "feature idea",
    "i have a feature idea",
    "let's spec",
    "lets spec",
    "let's brainstorm",
    "lets brainstorm",
)

# Dictation-mode opt-in phrases. Substring match, case-insensitive.
# Take precedence over design phrases (a single utterance containing
# BOTH a dictation phrase and a design phrase routes to dictation —
# the user explicitly chose the bypass). One utterance per session.
# See chunk 21 spec.
_DICTATION_OPTIN_PHRASES = (
    "dictate to claude",
    "tell claude directly",
    "send claude a message",
    "dictation mode",
    "skip design",
)

# Confirmation / cancellation phrases used by the dictation confirm step.
_DICTATION_CONFIRM_PHRASES = (
    "send it", "send this", "yes send", "confirmed", "confirm",
    "yes please", "go ahead", "ship it", "fire it off",
)
_DICTATION_CANCEL_PHRASES = (
    "scrap", "cancel", "nevermind", "never mind", "abort",
    "don't send", "do not send", "no don't", "forget it",
)

_MERGE_BRANCH_PHRASES = {
    "merge it", "merge this", "merge the branch", "merge that branch",
    "okay merge it", "ok merge it", "go ahead and merge", "let's merge it",
    "lets merge it",
}
_RESTART_SELF_PHRASES = {
    "restart yourself", "restart valet", "kick yourself", "reboot yourself",
    "bounce yourself", "restart the server", "kick the server",
}

# In-design fast-action phrases — only matched when a session is active.
_SHIP_DESIGN_PHRASES = {
    "ship", "ship it", "ship this", "ship now", "ship it now",
    "send it", "ok build it", "okay build it",
    "go ahead and build", "okay ship", "ok ship", "ship the design",
    "let's ship it", "lets ship it",
    # "commit"-flavored ship intents. Multi-word only — a bare "commit"
    # would let an innocuous design utterance ("commit to using React")
    # ship via the startswith() check below, so we never list it alone.
    "commit now", "commit it", "commit this", "commit it now",
    "commit the design",
}
_SCRAP_DESIGN_PHRASES = {
    "scrap this", "scrap that", "scrap the design", "start over",
    "throw this out", "throw it out", "forget the design", "drop the design",
    "cancel the design", "abandon this",
}
_SHOW_DRAFT_PHRASES = {
    "show me the prompt", "show the prompt", "what's the prompt",
    "whats the prompt", "read me the prompt", "read the prompt",
    "show me the draft", "show the draft",
}

# List-projects intent — verb+projects in any of several speech-shaped forms.
_LIST_PROJECTS_PATTERN = _action_re.compile(
    r'\b('
    r'(?:list|show|tell\s+me|name|enumerate)\s+(?:me\s+)?(?:the\s+|my\s+|all\s+(?:my\s+|the\s+)?)?projects?'
    r'|what\s+projects?\s+(?:do\s+i\s+have|can\s+you\s+see|are\s+there|do\s+you\s+know|you\s+can\s+see)'
    r'|(?:^|\s)(?:my|the)\s+projects?(?:\s|$|\?|\.)'
    r')\b',
    _action_re.IGNORECASE,
)

# Reserved app names that "open X" should route to OPEN_APP, not OPEN_PROJECT.
# Keeps "open cursor" / "open chrome" / "open terminal" out of the project resolver.
_OPEN_APP_NAMES = {
    # Native macOS apps
    "cursor", "chrome", "google chrome", "firefox", "safari", "terminal",
    "iterm", "iterm2", "warp", "vscode", "visual studio code", "code",
    "finder", "slack", "spotify", "notes", "mail", "messages", "calendar",
    "discord", "zoom", "obsidian", "xcode", "settings", "system settings",
    "desktop", "downloads", "documents", "music", "photos", "preview",
    # Google web apps — STT picks these up as "open my X" all the time
    "google", "gmail", "google mail", "google calendar", "google docs",
    "google drive", "google sheets", "google slides", "google meet",
    "calendar app",
    # Other common web apps users open by voice
    "youtube", "github", "linear", "notion", "figma", "claude", "chatgpt",
    "openai", "anthropic", "console",
    # VALET's own UI surfaces — keep "open the design panel" / "open the
    # process panel" from being misrouted as a project lookup.
    "design panel", "process panel", "settings panel", "the design panel",
    "the process panel", "the settings panel",
}

# Brand / category tokens that, when they appear anywhere in a captured name,
# strongly signal app intent rather than project intent. The smart matcher
# in _looks_like_app() splits the captured name on whitespace and checks each
# token against this set, which catches phrasings the literal exclusion list
# can't enumerate ("work gmail", "personal calendar", "my inbox", etc.). Keep
# this list tighter than _OPEN_APP_NAMES — we want strong signals only, not
# generic words like "code" or "messages" that could collide with project
# names ("code-review project", "messages-clone").
_APP_KEYWORDS = {
    "gmail", "calendar", "inbox", "email", "mail",
    "youtube", "github", "drive", "docs", "sheets", "slides", "meet",
    "google", "chrome", "safari", "firefox",
    "slack", "notion", "linear", "figma", "discord", "zoom",
    "panel",  # "design panel", "process panel", "settings panel"
}


def _looks_like_app(name: str) -> bool:
    """True if `name` (captured by an _OPEN_PROJECT_PATTERNS regex) is more
    plausibly a web/native app than a VALET project.

    Three-strike matcher:
      1. Exact (lowercased) match against _OPEN_APP_NAMES.
      2. Substring containment of an _OPEN_APP_NAMES entry — but only
         multi-word entries, so "open my google calendar" matches the
         "google calendar" entry without "code" tripping on "dharma code".
      3. Whitespace-token match against the curated _APP_KEYWORDS set —
         catches phrasings we can't enumerate ahead of time ("work gmail",
         "personal inbox"). _APP_KEYWORDS is intentionally tighter than
         _OPEN_APP_NAMES so ambiguous single words like "code" / "notes"
         / "music" don't false-positive on real project names.

    Returns False on no match, leaving the caller to route the name through
    open_project resolution as before.
    """
    n = name.lower().strip()
    # Strip trailing locational / filler phrases so an app name buried in natural
    # speech still matches: "notes on my computer" → "notes", "calendar app" →
    # "calendar". Without this, the extra words break the exact-match against
    # _OPEN_APP_NAMES and "open Notes on my computer" wrongly hits the project
    # resolver. These suffixes never appear in real project names.
    n = _action_re.sub(
        r"\s+(?:on|in)\s+(?:my|this|the)\s+"
        r"(?:computer|mac|macbook|laptop|desktop|machine)\s*$",
        "", n,
    )
    n = _action_re.sub(r"\s+app\s*$", "", n).strip()
    if not n:
        return False
    if n in _OPEN_APP_NAMES:
        return True
    # Multi-word entries only — single-word entries get the stricter token
    # check below so "code" can't substring-match inside "dharma code".
    for entry in _OPEN_APP_NAMES:
        if " " in entry and entry in n:
            return True
    tokens = n.split()
    if any(t in _APP_KEYWORDS for t in tokens):
        return True
    return False


def _weather_when(text: str) -> str:
    """Map a weather utterance to a forecast time scope for the spoken summary.

    Returns one of: "today" (default), "tomorrow", "day_after", "week".
    Order matters — "day after tomorrow" must be tested before "tomorrow",
    since it contains that substring.
    """
    t = (text or "").lower()
    if "day after tomorrow" in t:
        return "day_after"
    if "tomorrow" in t:
        return "tomorrow"
    if any(k in t for k in (
        "this week", "the week", "weekly", "week ahead", "rest of the week",
        "next few days", "coming days", "7 day", "seven day", "7-day", "next 7",
    )):
        return "week"
    return "today"


# UC3 universal-control voice patterns. "click on Submit" / "press the New Tab
# button" / "type my email into the address field". Targets resolve against a
# live observation, so a non-match just fails honestly — never a wild click.
_UI_CLICK_RE = re.compile(
    r'^(?:click|tap|press)\s+(?:on\s+|the\s+)?(?P<target>.+?)(?:\s+button)?\s*[.?!]*$',
    re.IGNORECASE)
_UI_TYPE_RE = re.compile(
    r'^type\s+(?P<text>.+?)\s+(?:in|into)\s+(?:the\s+)?(?P<field>.+?)(?:\s+(?:field|box|bar))?\s*[.?!]*$',
    re.IGNORECASE)


def detect_action_fast(text: str, ws=None) -> dict | None:
    """Keyword/regex-based action detection — ONLY for short, obvious commands.

    Everything else goes to the LLM which uses [ACTION:X] tags when it decides
    to act based on conversational understanding.

    Routing precedence (chunk 21):
      1. Dictation opt-in phrases  — highest priority. The user explicitly
         opted out of design and into "speak verbatim to Claude Code". A
         transcript containing BOTH a dictation phrase and a design phrase
         routes to dictation (user picked the bypass on purpose).
      2. Design opt-in phrases (regex + substring) — second priority. These
         are explicit design-intent signals and ARE allowed past the
         word-count gate below; without that exemption, the chunk-20 bug
         from 17:02:22 ("let's talk about a feature I want to add to
         Valet Dash Main", 13 words) would still slip through to the LLM.
      3. Word-count gate — everything below this gate is restricted to
         short, command-shaped utterances (≤ 12 words). Long messages are
         conversation, not commands, and route to the LLM router.
      4. In-session ship/scrap/show-draft (only when a design session is
         active on this ws). Phrases are tiny so the cap is not in play.
      5. Everything else.
    """
    t = text.lower().strip()
    words = t.split()

    # ── (1) Dictation mode opt-in — highest precedence, exempt from word cap.
    if any(p in t for p in _DICTATION_OPTIN_PHRASES):
        return {"action": "start_dictation"}

    # ── (2a) Greenfield design — "new project for X" matched BEFORE the
    # generic start-design pattern so "design a new project for X" doesn't
    # collapse to start_design topic="new project for X".
    nm = _NEW_PROJECT_DESIGN_PATTERN.match(text.strip())
    if nm:
        new_name = nm.group("name").strip()
        if new_name and new_name.lower() not in {"x", "something", "thing"}:
            return {"action": "start_design", "target": new_name, "new_project": True}

    # ── (2) Design-mode opt-in — exempt from word cap so naturally-phrased
    # intent like "let's talk about a feature I want to add to X" still
    # routes correctly. Regex path captures a topic; substring path doesn't
    # and falls through to the topic-prompt.
    m = _START_DESIGN_PATTERN.match(text.strip())
    if m:
        topic = m.group("topic").strip()
        if topic and topic.lower() not in {"tomorrow", "today", "this", "that", "it", "something"}:
            return {"action": "start_design", "target": topic}
    if any(p in t for p in _DESIGN_OPTIN_PHRASES):
        return {"action": "start_design", "target": ""}

    # ── (2.5) Sub-agent dispatch — exempt from word cap because real
    # agent-dispatch utterances often run long ("use the explore agent to
    # find every place that imports auth_helpers"). Routes to
    # _execute_dispatch_to_agent which composes + pastes.
    am = _AGENT_DISPATCH_PATTERN.match(text.strip())
    if am:
        agent_raw = am.group("agent").strip()
        task = am.group("task").strip()
        if agent_raw and task:
            return {"action": "dispatch_to_agent", "agent": agent_raw, "task": task}

    # ── (2c) UC3 universal control: "click on X" / "type X into the Y field".
    # Exempt from the word-count gate (a typed phrase can be long) — the verb
    # anchor keeps it command-shaped, and an unresolved target fails honestly.
    _tm = _UI_TYPE_RE.match(text.strip())
    if _tm:
        field = _tm.group("field").strip(" .?!")
        typed = _tm.group("text").strip()
        if field and typed:
            return {"action": "ui_act", "ui_action": "type", "target": field, "text": typed}
    _cm = _UI_CLICK_RE.match(text.strip())
    if _cm:
        tgt = _cm.group("target").strip(" .?!")
        if tgt and len(tgt.split()) <= 6:
            return {"action": "ui_act", "ui_action": "click", "target": tgt}

    # ── (3) Word-count gate for everything else.
    if len(words) > 12:
        return None  # Long messages are conversation, not commands

    # ── (4) In-design fast-actions (only when a session is active on ws).
    if ws is not None:
        import design_partner
        if design_partner.get_for_ws(ws) is not None:
            # Strip conversational filler so "please ship now" / "sure ship it
            # please" match the same as a bare "ship it". Without this, leading
            # words ("please", "sure", "okay") and trailing politeness
            # ("please", "for me") fell through to the LLM and the ship never
            # fired — the user said "ship" several times before it took.
            ts = t
            for _suf in (" please", " for me", " thanks", " thank you", " now please"):
                if ts.endswith(_suf):
                    ts = ts[: -len(_suf)].strip()
            _changed = True
            while _changed:
                _changed = False
                for _pre in (
                    "please ", "sure ", "ok ", "okay ", "yeah ", "yes ", "yep ",
                    "alright ", "all right ", "right ", "now ", "can you ",
                    "could you ", "would you ", "go ahead and ", "go ahead ",
                    "let's ", "lets ", "just ",
                ):
                    if ts.startswith(_pre):
                        ts = ts[len(_pre):].strip()
                        _changed = True

            def _phrase_hit(phrases) -> bool:
                return ts in phrases or any(ts.startswith(p + " ") for p in phrases)

            if _phrase_hit(_SHIP_DESIGN_PHRASES):
                return {"action": "ship_design"}
            if _phrase_hit(_SCRAP_DESIGN_PHRASES):
                return {"action": "scrap_design"}
            if _phrase_hit(_SHOW_DRAFT_PHRASES):
                return {"action": "show_draft"}

    # Close / dismiss the process panel. Fast-path so VALET responds
    # instantly without round-tripping through the LLM.
    if any(p in t for p in [
        "close it", "close that", "close the panel", "close panel",
        "dismiss it", "dismiss that", "dismiss the panel", "dismiss",
        "hide it", "hide that", "hide the panel",
    ]):
        return {"action": "close_panel"}

    # Screen requests — checked BEFORE project matching to prevent misrouting
    if any(p in t for p in ["look at my screen", "what's on my screen", "whats on my screen",
                             "what am i looking at", "what do you see", "see my screen",
                             "what's running on my", "whats running on my", "check my screen"]):
        return {"action": "describe_screen"}

    # Terminal / Claude Code — explicit open requests
    if any(w in t for w in ["open claude", "start claude", "launch claude", "run claude"]):
        return {"action": "open_terminal"}

    # Show recent build
    if any(w in t for w in ["show me what you built", "pull up what you made", "open what you built"]):
        return {"action": "show_recent"}

    # Screen awareness — explicit look/see requests
    if any(p in t for p in ["what's on my screen", "whats on my screen", "what do you see",
                             "can you see my screen", "look at my screen", "what am i looking at",
                             "what's open", "whats open", "what apps are open"]):
        return {"action": "describe_screen"}

    # "open/launch my calendar|email|notes" → LAUNCH the app (not the read
    # lookup). Checked before the calendar/mail lookups below, which match on
    # substrings like "my calendar" and would otherwise intercept "open ...".
    _oa = _OPEN_APP_LAUNCH_RE.match(t)
    if _oa:
        return {"action": "open_app", "target": _OPEN_APP_LAUNCH_MAP[_oa.group(1).lower()]}

    # "open / go to my <title> note" → show it in Notes.app (before the web route
    # so a "… note" request is never mistaken for a website).
    _on = _OPEN_NOTE_RE.match(t)
    if _on:
        _nt = (_on.group("title") or "").strip()
        if _nt and _nt.lower() not in ("a", "the", "this", "that", "another", "new", "my", "your", "me", "it"):
            return {"action": "open_note", "target": _nt}

    # Web destinations: "open my gmail", "open google analytics in chrome",
    # "go to github". Only fires when a URL resolves OR a browser was named, so
    # "open Spotify" / "open <project>" still fall through to app/project routing.
    _ow = _OPEN_WEB_RE.match(t)
    if _ow:
        _wt = (_ow.group("target") or "").strip()
        _wb = _OPEN_BROWSER_MAP.get((_ow.group("browser") or "").strip().lower(), "")
        _url = _resolve_web_url(_wt)
        if _url or _wb:
            if not _url:  # browser named but destination unknown → search it
                from urllib.parse import quote
                _url = f"https://www.google.com/search?q={quote(_wt)}"
            return {"action": "open_url", "target": _url,
                    "browser": _wb or "chrome", "label": _wt}

    # Calendar — explicit schedule requests
    if any(p in t for p in ["what's my schedule", "whats my schedule", "what's on my calendar",
                             "whats on my calendar", "do i have any meetings", "any meetings",
                             "what's next on my calendar", "my schedule today",
                             "what do i have today", "my calendar", "upcoming meetings",
                             "next meeting", "what's my next meeting"]):
        return {"action": "check_calendar"}

    # Mail — explicit email requests
    if any(p in t for p in ["check my email", "check my mail", "any new emails", "any new mail",
                             "unread emails", "unread mail", "what's in my inbox",
                             "whats in my inbox", "read my email", "read my mail",
                             "any emails", "any mail", "email update", "mail update"]):
        return {"action": "check_mail"}

    # Weather — capture an optional location with the regex variant, fall
    # through to substring variants for empty-target ("use my hometown")
    # phrasings. New native pipeline beats the slow RESEARCH WebFetch path.
    #
    # Both the "weather in/for/at X" and "forecast for/in/at X" regexes can
    # capture a time-scope word as if it were a place ("weather for tomorrow"
    # → "tomorrow"), which then fails to geocode ("I can't place tomorrow on
    # the map"). Reject these so they fall through to the empty-target bucket,
    # where they correctly mean "the user's hometown, for that time scope."
    _TIME_SCOPE = {
        "today", "tomorrow", "tonight", "now", "right now", "later", "this morning",
        "this afternoon", "this evening", "the morning", "the afternoon", "the evening",
        "the day", "the week", "this week", "next week", "the next week", "the weekend",
        "this weekend", "the next few days", "the rest of today", "the rest of the day",
        "the rest of the week", "the rest of the weekend",
    }
    # Self-referential location phrases ("my hometown", "here", "outside") all
    # mean "the user's hometown" — empty target so the HOMETOWN_CITY fallback
    # fires. WITHOUT this, "weather for my hometown" captured "my hometown",
    # whose geocode degradation stripped to the bare token "my" and matched an
    # obscure real place named "My" — VALET then said "The forecast for My,
    # sir" and mounted a card for the wrong spot.
    _SELF_LOC = {
        "my hometown", "my home town", "my home", "my house", "my area",
        "my town", "my city", "my place", "my location", "my region",
        "my neighborhood", "my neighbourhood", "my spot", "home", "here",
        "around here", "round here", "outside", "out", "out there",
        "where i am", "where i live", "this area", "the area",
    }

    def _wx_target(cand: str) -> str:
        """Empty (→ hometown) for time-scope / self-referential phrases."""
        c = (cand or "").strip()
        low = c.lower()
        if not c or low in _TIME_SCOPE or low in _SELF_LOC:
            return ""
        # "my hometown today", "here right now" — leading possessive/self
        # reference with a trailing time scope still means the hometown.
        if low.startswith(("my ", "here ", "outside ", "around here ")):
            return ""
        return c

    wm = _action_re.match(r".*\bweather\s+(?:in|for|at)\s+(.+?)\s*\.?\s*$", t)
    if wm:
        # A weather query for sure; the token after for/in/at is either a place
        # or just a time scope / self-reference ("for tomorrow", "for my town").
        return {"action": "check_weather",
                "target": _wx_target(wm.group(1)),
                "when": _weather_when(t)}
    fm = _action_re.match(r".*\bforecast\s+(?:for|in|at)\s+(.+?)\s*\.?\s*$", t)
    if fm:
        return {"action": "check_weather",
                "target": _wx_target(fm.group(1)),
                "when": _weather_when(t)}
    if any(p in t for p in [
        "what's the weather", "whats the weather", "weather today", "weather right now",
        "weather like", "current weather", "forecast for today", "forecast for tomorrow",
        "forecast for the week", "weekly forecast", "seven day forecast", "7 day forecast",
        "uv index", "uv warning", "uv warnings", "any uv", "weather warning",
        "weather warnings", "any weather warnings", "is it going to rain", "will it rain",
        "how hot", "how cold", "temperature outside", "temperature out", "feels like outside",
    ]):
        return {"action": "check_weather", "target": "", "when": _weather_when(t)}

    # Dispatch / build status check
    if any(p in t for p in ["where are we", "where were we", "project status", "how's the build",
                             "hows the build", "status update", "status report", "where is that",
                             "how's it going with", "hows it going with", "is it done",
                             "is that done", "what happened with"]):
        return {"action": "check_dispatch"}

    # Task list check
    if any(p in t for p in ["what's on my list", "whats on my list", "my tasks", "my to do",
                             "my todo", "what do i need to do", "open tasks", "task list"]):
        return {"action": "check_tasks"}

    # List of known projects — regex captures "list/show/tell me/name/enumerate projects",
    # "what projects can you see / do I have", "my projects", etc.
    if _LIST_PROJECTS_PATTERN.search(t):
        return {"action": "list_projects"}

    # Open a named project — regex captures "open X", "open the X project",
    # "open my X project in cursor", "can you open X", "open the project called X".
    # Skipped when the captured name looks like an app (so "open Cursor",
    # "open my work gmail", "open the design panel" all route through the
    # LLM's OPEN_APP path instead of erroring on a missing project).
    for pat in _OPEN_PROJECT_PATTERNS:
        m = pat.match(t)
        if m:
            name = m.group("name").strip()
            if name and not _looks_like_app(name):
                return {"action": "open_project", "target": name}
            break  # Matched as "open <app>" — let the LLM handle via OPEN_APP

    # Register a path → alias for projects outside any configured root.
    # Match against the ORIGINAL text (not lowercased `t`) so absolute paths
    # like /Users/Finley/foo keep their case.
    for pat in _REGISTER_PROJECT_PATTERNS:
        m = pat.match(text.strip())
        if m:
            gd = m.groupdict()
            return {
                "action": "register_project",
                "target": gd.get("path", "").strip(),
                "alias": (gd.get("alias") or "").strip(),
            }

    # Re-read warm context for the active project
    if any(p in t for p in ["refresh context", "refresh the context", "reload context",
                             "reload the context", "re-read context", "rescan context"]):
        return {"action": "refresh_context"}

    # Phase 5 self-mod ops — merge it / restart yourself. Both gated to
    # exact-phrase matches (no LLM round-trip).
    if t in _MERGE_BRANCH_PHRASES or any(t.startswith(p + " ") for p in _MERGE_BRANCH_PHRASES):
        return {"action": "merge_branch"}
    if t in _RESTART_SELF_PHRASES or any(t.startswith(p + " ") for p in _RESTART_SELF_PHRASES):
        return {"action": "restart_self"}

    # Usage / cost check
    if any(p in t for p in ["usage", "how much have you cost", "how much am i spending",
                             "what's the cost", "whats the cost", "api cost", "token usage",
                             "how expensive", "what's my bill"]):
        return {"action": "check_usage"}

    return None  # Everything else goes to the LLM for conversational routing


# -- Action Handlers -------------------------------------------------------

async def handle_open_terminal() -> str:
    result = await open_terminal("claude --dangerously-skip-permissions")
    return result["confirmation"]


async def handle_build(target: str) -> str:
    async with process_bus.task_context(f"Building: {target[:60]}") as task_id:
        name = _generate_project_name(target)
        path = str(Path.home() / "Desktop" / name)
        os.makedirs(path, exist_ok=True)
        await emit_step(task_id, f"Created project folder: {name}", detail=path)

        # Write CLAUDE.md with clear instructions
        claude_md = Path(path) / "CLAUDE.md"
        claude_md.write_text(f"# Task\n\n{target}\n\nBuild this completely. If web app, make index.html work standalone.\n")

        # Write prompt to a file, then pipe it to claude -p
        # This avoids all shell escaping issues
        prompt_file = Path(path) / ".valet_prompt.txt"
        prompt_file.write_text(target)
        await emit_step(task_id, "Prompt staged for Claude Code")

        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "cd {path} && cat .valet_prompt.txt | claude -p --dangerously-skip-permissions"\n'
            "end tell"
        )
        await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await emit_step(task_id, "Claude Code spawned in Terminal", status="done")

        recently_built.append({"name": name, "path": path, "time": time.time()})
        return f"On it, sir. Claude Code is working in {name}."


async def handle_show_recent() -> str:
    if not recently_built:
        return "Nothing built recently, sir."
    last = recently_built[-1]
    project_path = Path(last["path"])

    # Try to find the best file to open
    for name in ["report.html", "index.html"]:
        f = project_path / name
        if f.exists():
            await open_browser(f"file://{f}")
            return f"Opened {name} from {last['name']}, sir."

    # Try any HTML file
    html_files = list(project_path.glob("*.html"))
    if html_files:
        await open_browser(f"file://{html_files[0]}")
        return f"Opened {html_files[0].name} from {last['name']}, sir."

    # Fall back to opening the folder in Finder
    script = f'tell application "Finder"\nactivate\nopen POSIX file "{last["path"]}"\nend tell'
    await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    return f"Opened the {last['name']} folder in Finder, sir."


# ---------------------------------------------------------------------------
# Background lookup system — spawns slow tasks, reports back via voice
# ---------------------------------------------------------------------------

# Track active lookups so VALET can report status
_active_lookups: dict[str, dict] = {}  # id -> {"type": str, "status": str, "started": float}


async def _lookup_and_report(lookup_type: str, lookup_fn, ws, history: list[dict] = None, voice_state: dict = None):
    """Run a slow lookup, then speak the result back.

    VALET stays conversational — this runs completely off the main path.
    """
    lookup_id = str(uuid.uuid4())[:8]
    dispatch_time = time.time()
    _active_lookups[lookup_id] = {
        "type": lookup_type,
        "status": "working",
        "started": dispatch_time,
    }

    try:
        # Run the async lookup directly — these functions already use
        # asyncio.create_subprocess_exec so they don't block the event loop
        result_text = await asyncio.wait_for(
            lookup_fn(),
            timeout=30,
        )

        _active_lookups[lookup_id]["status"] = "done"
        _track_task(lookup_type, True, time.time() - dispatch_time)
        if lookup_type in _connections_seen:
            _connections_seen[lookup_type] = True

        # Speak the result — skip audio only if the user spoke *again* after we
        # dispatched this lookup (a fresh utterance we'd talk over). The triggering
        # utterance itself must not suppress its own answer: native fast paths like
        # weather finish inside the old 3s window, which muted every result.
        if voice_state and voice_state["last_user_time"] > dispatch_time:
            log.info(f"Skipping lookup audio for {lookup_type} — newer user utterance after dispatch")
            # Result is still stored in history below
        else:
            tts = strip_markdown_for_tts(result_text)
            audio = await synthesize_speech(tts)
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                if audio:
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": result_text})
                else:
                    await ws.send_json({"type": "text", "text": result_text})
                await ws.send_json({"type": "status", "state": "idle"})
            except Exception:
                pass

        log.info(f"Lookup {lookup_type} complete: {result_text[:80]}")

        # Store lookup result in conversation history so VALET remembers it
        if history is not None:
            history.append({"role": "assistant", "content": f"[{lookup_type} check]: {result_text}"})

    except asyncio.TimeoutError:
        _active_lookups[lookup_id]["status"] = "timeout"
        _track_task(lookup_type, False, time.time() - dispatch_time)
        try:
            fallback = f"That {lookup_type} check is taking too long, sir. The data may still be syncing."
            audio = await synthesize_speech(fallback)
            await ws.send_json({"type": "status", "state": "speaking"})
            if audio:
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": fallback})
            await ws.send_json({"type": "status", "state": "idle"})
        except Exception:
            pass
    except Exception as e:
        _active_lookups[lookup_id]["status"] = "error"
        _track_task(lookup_type, False, time.time() - dispatch_time)
        log.warning(f"Lookup {lookup_type} failed: {e}")
    finally:
        # Clean up after 60s
        await asyncio.sleep(60)
        _active_lookups.pop(lookup_id, None)


def _format_apple_cal(events: list[dict], label: str) -> str:
    """Voice summary for Apple Calendar (EventKit) events."""
    if not events:
        return f"Nothing on your calendar {label}, sir."
    parts = []
    for e in events[:6]:
        when = "all day" if e.get("all_day") else (e.get("time_str") or "").strip()
        parts.append(f"{e['title']} at {when}" if when and when != "all day"
                     else f"{e['title']} (all day)" if when == "all day" else e["title"])
    n = len(events)
    extra = f" and {n - 6} more" if n > 6 else ""
    return f"You have {n} {'event' if n == 1 else 'events'} {label}, sir: " + "; ".join(parts) + extra + "."


def _merge_calendar_events(apple: list[dict], google: list[dict]) -> list[dict]:
    """Merge Apple (EventKit) + Google event lists, de-duplicating the overlap.
    A Google account commonly syncs into macOS Calendar, so the same event shows
    in both — dedup on a normalized (title, time) key. All-day events key on the
    date; timed events on the UTC minute (both sources expose start_iso in UTC)."""
    import re

    def key(e: dict) -> tuple:
        title = re.sub(r"[^a-z0-9]", "", (e.get("title") or "").lower())
        digits = re.sub(r"\D", "", e.get("start_iso") or "")
        when = digits[:8] if e.get("all_day") else digits[:12]
        return (title, when)

    seen, merged = set(), []
    for e in list(apple or []) + list(google or []):
        k = key(e)
        if k in seen:
            continue
        seen.add(k)
        merged.append(e)
    merged.sort(key=lambda x: x.get("start_iso") or "")
    return merged


async def _read_calendar_merged(date_str: str | None = None) -> list[dict]:
    """Events from Apple + Google, merged + deduped. Apple is the always-local
    source (full access); Google layers on when connected. Either source failing
    degrades to the other instead of erroring."""
    import apple_calendar
    apple_events = []
    if apple_calendar.auth_status() == 3:
        try:
            apple_events = apple_calendar.read_events(date_str)
        except Exception as e:
            log.warning(f"apple read_events failed: {e}")
    google_events = []
    try:
        import google_calendar
        google_events = await google_calendar.read_events(date_str)
    except Exception as e:
        log.warning(f"google read_events failed: {e}")
    return _merge_calendar_events(apple_events, google_events)


async def _do_calendar_lookup() -> str:
    """Read today's events from Apple Calendar (EventKit) + Google, merged."""
    import apple_calendar
    if apple_calendar.auth_status() != 3 and not google_auth.is_connected():
        return ("I don't have Calendar access yet, sir — grant Calendar under "
                "Settings, Permissions, or connect Google in Settings, then ask again.")
    events = await _read_calendar_merged()  # today
    if events:
        _ctx_cache["calendar"] = "; ".join(
            f"{e['title']}{(' at ' + e['time_str']) if e.get('time_str') else ''}" for e in events
        )
    return _format_apple_cal(events, "today")


async def _do_weather_lookup(location: str, ws, when: str = "today") -> str:
    """Native weather lookup for [ACTION:CHECK_WEATHER] / check_weather fast-path.

    Resolves location (or falls back to HOMETOWN_CITY → ADDRESS), geocodes,
    fetches Open-Meteo forecast, and mounts the weather panel on the frontend.

    Render-only: the forecast detail lives in the panel (current conditions,
    today's outlook, 7-day strip, UV, severe-weather banner), so VALET does
    NOT read it aloud. This function returns a short butler acknowledgment for
    TTS instead — escalated to include the text of a *severe* alert, since
    that's worth hearing without looking at the screen. The `when` arg is
    retained for caller compatibility; the panel always shows all 7 days.

    The card emission is the substantive effect; the return is just the spoken
    acknowledgment.
    """
    import weather as _wx
    import uuid as _uuid

    target = (location or "").strip()
    # Self-referential phrases ("my hometown", "here", "outside") mean the
    # user's hometown, not a place to geocode. The fast-path normalizes these,
    # but the LLM embedded-action path passes the raw tag target through, so
    # guard here too — otherwise geocode strips "my hometown" → "my" and hits
    # an obscure real place named "My".
    _self_ref = {
        "my hometown", "my home town", "my home", "my house", "my area",
        "my town", "my city", "my place", "my location", "my region",
        "my neighborhood", "my neighbourhood", "home", "here", "around here",
        "outside", "where i am", "where i live", "this area", "the area",
    }
    if target.lower() in _self_ref or target.lower().startswith("my "):
        target = ""
    if not target:
        # Fallback chain: HOMETOWN_CITY → ADDRESS → ask
        _, env = _read_env()
        target = (env.get("HOMETOWN_CITY") or "").strip()
        if not target:
            target = (env.get("ADDRESS") or "").strip()
    if not target:
        return "I don't have a hometown on file, sir. Set one in settings or name a city."

    geo = await _wx.geocode(target)
    if not geo:
        return f"I can't place {target} on the map, sir. Could you spell it?"

    fc = await _wx.fetch_forecast(geo["lat"], geo["lon"], geo["timezone"])
    if not fc:
        return f"Couldn't reach the forecast for {geo['name']}, sir."

    alert = _wx.synthesize_alert(fc.get("daily", {}), fc.get("current", {}))
    card = _wx.build_card_payload(fc, geo["name"])

    # Cache the rich shape so the LLM's dynamic-context block also knows the
    # latest conditions for the next non-weather turn.
    _ctx_cache["weather"] = {
        "summary": _wx.quick_summary_for_prompt(fc, geo["name"]),
        "raw": fc,
        "location": geo["name"],
    }

    # Floating card on the frontend. result.weather is a ProcessEvent type the
    # processPanel card builder mounts via the existing floatingLayer. It MUST
    # go through the bus so it's wrapped in a {"type":"process_event",...} frame
    # — main.ts only routes process_event/design_event/etc. and silently drops a
    # raw top-level {"type":"result.weather"} frame, which is why the panel
    # never appeared.
    try:
        await process_bus.emit(ProcessEvent(
            type="result.weather",
            task_id="weather",
            id=_uuid.uuid4().hex[:10],
            status="done",
            title=f"Weather: {geo['name']}",
            payload=card,
        ))
    except Exception as e:
        log.debug(f"result.weather emit failed: {e}")

    # Render-only: the panel carries the forecast, so speak only a brief
    # acknowledgment. A severe alert is surfaced aloud anyway — safety detail
    # the user shouldn't have to look up to hear.
    ack = f"The forecast for {geo['name']}, sir."
    if alert and alert.get("level") == "severe" and alert.get("text"):
        ack = f"The forecast for {geo['name']}, sir. {alert['text']}"
    return ack


async def _do_mail_lookup() -> str:
    """Slow mail fetch — runs in thread."""
    unread_info = await get_unread_count()
    if isinstance(unread_info, dict):
        _ctx_cache["mail"] = format_unread_summary(unread_info)
        if unread_info["total"] == 0:
            return "Inbox is clear, sir. No unread messages."
        unread_msgs = await get_unread_messages(count=5)
        summary = format_unread_summary(unread_info)
        if unread_msgs:
            top = unread_msgs[:3]
            details = ". ".join(
                f"{_short_sender(m['sender'])} regarding {m['subject']}"
                for m in top
            )
            return f"{summary} Most recent: {details}."
        return summary
    return "Couldn't reach Mail at the moment, sir."


async def _do_screen_lookup() -> str:
    """Screen describe — UC2 focused-window observation (window screenshot + the
    AX element snapshot) sent to the model via the proxy. Falls back to the
    whole-display describe, then to a window-list summary."""
    if anthropic_client:
        try:
            import perception
            obs = await perception.build_observation(executor)
            if obs.get("image") or obs.get("elements"):
                return await perception.describe_observation(obs, anthropic_client)
        except Exception as e:
            log.warning(f"perception observation failed, falling back: {e}")
        return await describe_screen(anthropic_client)
    windows = await get_active_windows()
    if windows:
        apps = set(w["app"] for w in windows)
        active = next((w for w in windows if w["frontmost"]), None)
        result = f"You have {', '.join(apps)} open."
        if active:
            result += f" Currently focused on {active['app']}: {active['title']}."
        return result
    return "Couldn't see the screen, sir."


def get_lookup_status() -> str:
    """Get status of active lookups for when user asks 'how's that coming'."""
    if not _active_lookups:
        return ""
    active = [v for v in _active_lookups.values() if v["status"] == "working"]
    if not active:
        return ""
    parts = []
    for lookup in active:
        elapsed = int(time.time() - lookup["started"])
        parts.append(f"{lookup['type']} check ({elapsed}s)")
    return "Currently working on: " + ", ".join(parts)


def _short_sender(sender: str) -> str:
    """Extract just the name from an email sender string."""
    if "<" in sender:
        return sender.split("<")[0].strip().strip('"')
    if "@" in sender:
        return sender.split("@")[0]
    return sender


async def handle_browse(text: str, target: str) -> str:
    """Open a URL directly or search. Smart about detecting URLs in speech."""
    import re
    from urllib.parse import quote

    browser = "firefox" if "firefox" in text.lower() else "chrome"
    combined = text.lower()

    # 1. Try to find a URL or domain in the text
    # Match things like "joetmd.com", "google.com/maps", "https://example.com"
    url_pattern = r'(?:https?://)?(?:www\.)?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+(?:/[^\s]*)?)'
    url_match = re.search(url_pattern, text, re.IGNORECASE)

    if url_match:
        domain = url_match.group(0)
        if not domain.startswith("http"):
            domain = "https://" + domain
        await open_browser(domain, browser)
        return f"Opened {url_match.group(0)}, sir."

    # 2. Check for spoken domains that speech-to-text mangled
    # "Joe tmd.com" → "joetmd.com", "roofo.co" etc.
    # Try joining words that end/start with a dot pattern
    words = text.split()
    for i, word in enumerate(words):
        # Look for word ending with common TLD
        if re.search(r'\.(com|co|io|ai|org|net|dev|app)$', word, re.IGNORECASE):
            # This word IS a domain — might have spaces before it
            domain = word
            # Check if previous word should be joined (e.g., "Joe tmd.com" → "joetmd.com" is tricky)
            if not domain.startswith("http"):
                domain = "https://" + domain
            await open_browser(domain, browser)
            return f"Opened {word}, sir."

    # 3. Fall back to Google search with cleaned query
    query = target
    for prefix in ["search for", "look up", "google", "find me", "pull up", "open chrome",
                    "open firefox", "open browser", "go to", "can you", "in the browser",
                    "can you go to", "please"]:
        query = query.lower().replace(prefix, "").strip()
    # Remove filler words
    query = re.sub(r'\b(can|you|the|in|to|a|an|for|me|my|please)\b', '', query).strip()
    query = re.sub(r'\s+', ' ', query).strip()

    if not query:
        query = target

    url = f"https://www.google.com/search?q={quote(query)}"
    await open_browser(url, browser)
    return "Searching for that, sir."


# -- Session Summary (Three-Tier Memory) -----------------------------------

async def _update_session_summary(
    old_summary: str,
    rotated_messages: list[dict],
    client: anthropic.AsyncAnthropic,
) -> str:
    """Background Haiku call to update the rolling session summary."""
    prompt = f"""Update this conversation summary to include the new messages.

Current summary: {old_summary or '(start of conversation)'}

New messages to incorporate:
{chr(10).join(f'{m["role"]}: {m["content"][:200]}' for m in rotated_messages)}

Write an updated summary in 2-4 sentences capturing the key topics, decisions, and context. Be concise."""

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        log.warning(f"Summary update failed: {e}")
        return old_summary  # Keep old summary on failure


# -- WebSocket Voice Handler -----------------------------------------------

# Substring-match allowlist used to abort a running long-task (e.g. native
# research) when ambient transcripts otherwise interrupt the agent. See
# docs/design_partner_tests.md → "Research mute test" for the live protocol.
_RESEARCH_CANCEL_WORDS = ("cancel", "stop", "nevermind", "never mind")


def _is_cancel_phrase(user_text: str) -> bool:
    t = user_text.lower()
    return any(w in t for w in _RESEARCH_CANCEL_WORDS)



@app.websocket("/ws/voice")
async def voice_handler(ws: WebSocket):
    """
    WebSocket protocol:

    Client -> Server:
        {"type": "transcript", "text": "...", "isFinal": true}

    Server -> Client:
        {"type": "audio", "data": "<base64 mp3>", "text": "spoken text"}
        {"type": "status", "state": "thinking"|"speaking"|"idle"|"working"}
        {"type": "task_spawned", "task_id": "...", "prompt": "..."}
        {"type": "task_complete", "task_id": "...", "summary": "..."}
    """
    await ws.accept()
    task_manager.register_websocket(ws)
    await process_bus.subscribe(ws)
    # Per-connection Langfuse scope: groups every trace from this conversation
    # under one session_id (and attributes them to the user) in the Sessions
    # view. No-op when tracing is disabled. Closed in the finally below.
    conversation_id = uuid.uuid4().hex
    _obs_scope = observability.connection_scope(
        session_id=conversation_id, user_id=USER_NAME, tags=["voice"]
    ).enter()
    history: list[dict] = []
    work_session = WorkSession()
    planner = TaskPlanner()

    # Response cancellation — when new input arrives, cancel current response
    _current_response_id = 0
    _cancel_response = False

    # Long-running native research task — set when an [ACTION:RESEARCH] is
    # dispatched, cleared (.done()) when the task completes. Used by the
    # transcript intercept below to suppress ambient speech and honor only
    # the cancel-word allowlist while research is in flight.
    active_research_task: Optional[asyncio.Task] = None

    # Dictation mode state (chunk 21 Mode 2). Two phases:
    #   "capturing_prompt"   — waiting for the user's next utterance to
    #                          become the prompt that's sent to Cursor.
    #   "confirming"         — prompt is captured; waiting for the user
    #                          to confirm or cancel. captured_prompt
    #                          holds the verbatim text.
    # When phase is None, dictation is inactive and transcripts route
    # normally.
    dictation_phase: Optional[str] = None
    dictation_captured_prompt: str = ""

    # Audio collision prevention — track when user last spoke
    voice_state = {"last_user_time": 0.0}

    # Self-awareness — track last spoken response to avoid repetition
    last_valet_response = ""

    # Three-tier conversation memory
    session_buffer: list[dict] = []  # ALL messages, never truncated
    session_summary: str = ""  # Rolling summary of older conversation
    summary_update_pending: bool = False
    messages_since_last_summary: int = 0

    log.info("Voice WebSocket connected")

    try:
        # ── Greeting — always start in conversation mode ──
        now = datetime.now()
        hour = now.hour
        if hour < 12:
            greeting = "Good morning, sir."
        elif hour < 17:
            greeting = "Good afternoon, sir."
        else:
            greeting = "Good evening, sir."

        global _last_greeting_time
        should_greet = (time.time() - _last_greeting_time) > 60

        if should_greet:
            _last_greeting_time = time.time()

            async def _send_greeting():
                try:
                    audio_bytes = await synthesize_speech(greeting)
                    if audio_bytes:
                        encoded = base64.b64encode(audio_bytes).decode()
                        await ws.send_json({"type": "status", "state": "speaking"})
                        await ws.send_json({"type": "audio", "data": encoded, "text": greeting})
                        history.append({"role": "assistant", "content": greeting})
                        log.info(f"VALET: {greeting}")
                        await ws.send_json({"type": "status", "state": "idle"})
                except Exception as e:
                    log.warning(f"Greeting failed: {e}")

            asyncio.create_task(_send_greeting())

        try:
            await ws.send_json({"type": "status", "state": "idle"})
        except Exception:
            return  # WebSocket already gone

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # ── Risk-tier confirmation response (Stage D) ──
            if msg.get("type") == "confirm_response":
                confirmations.resolve(msg.get("id", ""), bool(msg.get("allow")))
                continue

            # ── Global kill switch (Stage D): halt actions + the assistant loop ──
            if msg.get("type") == "kill":
                kill_switch.engage()
                confirmations.cancel_all(allow=False)  # deny anything awaiting a decision
                _cancel_response = True                 # stop the in-progress reply
                await task_manager._notify({"type": "kill_state", "engaged": True})
                continue
            if msg.get("type") == "kill_reset":
                kill_switch.reset()
                await task_manager._notify({"type": "kill_state", "engaged": False})
                continue

            # ── Fix-self: activate work mode in VALET repo ──
            if msg.get("type") == "fix_self":
                valet_dir = str(Path(__file__).parent)
                await work_session.start(valet_dir)
                response_text = "Work mode active in my own repo, sir. Tell me what needs fixing."
                tts = strip_markdown_for_tts(response_text)
                await ws.send_json({"type": "status", "state": "speaking"})
                audio = await synthesize_speech(tts)
                if audio:
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": response_text})
                else:
                    await ws.send_json({"type": "text", "text": response_text})
                continue

            # ── Greenfield mode: user picked "+ New project…" in the design
            # panel target dropdown. Attaches a project name (+ optional
            # base dir) to the active design session. Ship-it later
            # scaffolds + git-inits + opens Cursor before pasting.
            if msg.get("type") == "set_design_new_project":
                proj_name = (msg.get("name") or "").strip()
                base_dir = (msg.get("base_dir") or "").strip()
                if not proj_name:
                    continue
                try:
                    import design_partner
                    session = design_partner.get_for_ws(ws)
                    if session is None:
                        await _speak(ws, "No design session active, sir.")
                        continue
                    # Slug-clean the name so AppleScript window matching +
                    # filesystem paths don't break on quotes/special chars.
                    import re as _gn_re
                    clean = _gn_re.sub(r"[^A-Za-z0-9._\- ]+", "", proj_name).strip().replace(" ", "-")
                    session.new_project_name = clean or proj_name
                    session.new_project_base_dir = base_dir or None
                    # Clear any prior existing-project target.
                    session.project_path = None
                    await session.emit_state()
                    where = f" in {base_dir}" if base_dir else ""
                    await _speak(ws, f"New project {session.new_project_name}{where}, sir. What's the stack?")
                    log.info(
                        "set_design_new_project: session=%s name=%r base=%r",
                        session.id, session.new_project_name, session.new_project_base_dir,
                    )
                except Exception as e:
                    log.error(f"set_design_new_project failed: {e}")
                continue

            # ── Manual agent attach for the active design session.
            # Sent by the design panel's agent dropdown. Empty string / "auto"
            # means let the ship handler auto-detect from draft content via
            # agents.auto_detect_agent.
            if msg.get("type") == "set_design_agent":
                agent_name = (msg.get("agent") or "").strip()
                if agent_name.lower() == "auto":
                    agent_name = ""
                try:
                    import design_partner
                    session = design_partner.get_for_ws(ws)
                    if session is None:
                        continue
                    session.agent = agent_name or None
                    await session.emit_state()
                    log.info(f"set_design_agent: session={session.id} agent={agent_name!r}")
                except Exception as e:
                    log.error(f"set_design_agent failed: {e}")
                continue

            # ── Manual target attach for the active design session.
            # Sent by the design panel's project dropdown so the user can
            # bypass voice project-resolution (which mishears hyphens,
            # confuses "main" with "maine", etc.) and pick the build target
            # by hand. No-op if no design session is active on this WS.
            if msg.get("type") == "set_design_target":
                target_path = (msg.get("path") or "").strip()
                if not target_path:
                    continue
                try:
                    import design_partner
                    session = design_partner.get_for_ws(ws)
                    if session is None:
                        await _speak(ws, "No design session active, sir. Say 'let's design' first.")
                        continue
                    p = Path(target_path).expanduser().resolve()
                    if not p.exists() or not p.is_dir():
                        await _speak(ws, f"That path isn't a directory, sir: {p}")
                        continue
                    session.project_path = p
                    await session.emit_state()
                    await _speak(ws, f"Target set to {p.name}, sir.")
                except Exception as e:
                    log.error(f"set_design_target failed: {e}")
                continue

            if msg.get("type") != "transcript" or not msg.get("isFinal"):
                continue

            user_text = apply_speech_corrections(msg.get("text", "").strip())
            if not user_text:
                continue

            # Cancel any in-flight response
            _current_response_id += 1
            my_response_id = _current_response_id
            _cancel_response = True
            await asyncio.sleep(0.05)  # Let any pending sends notice the cancellation
            _cancel_response = False

            voice_state["last_user_time"] = time.time()
            log.info(f"User: {user_text}")

            # ── Dictation mode capture/confirm intercept (chunk 21 Mode 2).
            # When ws is in a dictation phase, the user's utterance is the
            # prompt body (or a confirm/cancel) — NOT a routable command. We
            # short-circuit the entire action pipeline.
            current_phase = getattr(ws, "dictation_phase", None)
            if current_phase == "capturing_prompt":
                t_lower = user_text.lower()
                if any(p in t_lower for p in _DICTATION_CANCEL_PHRASES):
                    asyncio.create_task(_execute_cancel_dictation(ws))
                    continue
                # Capture verbatim, move to confirm phase, read back.
                ws.dictation_captured_prompt = user_text
                ws.dictation_phase = "confirming"
                try:
                    await ws.send_json({
                        "type": "dictation_event",
                        "event": {"state": "confirming", "prompt": user_text},
                    })
                except Exception:
                    pass
                # Bound the read-back so a runaway transcript isn't spoken
                # in full; user can still review the full text in the panel.
                preview = user_text if len(user_text) <= 240 else user_text[:235] + "…"
                await _speak(ws, f"Got it: \"{preview}\" Send this, sir?")
                continue
            if current_phase == "confirming":
                t_lower = user_text.lower()
                if any(p in t_lower for p in _DICTATION_CANCEL_PHRASES):
                    asyncio.create_task(_execute_cancel_dictation(ws))
                    continue
                if any(p in t_lower for p in _DICTATION_CONFIRM_PHRASES):
                    prompt = getattr(ws, "dictation_captured_prompt", "") or ""
                    asyncio.create_task(_execute_confirm_dictation(ws, prompt))
                    continue
                # Ambiguous reply — re-prompt without leaving confirming.
                await _speak(
                    ws,
                    'Sorry, sir — say "send it" to confirm or "cancel" to scrap.',
                )
                continue

            # Mute action routing while native research is in flight. Without
            # this, ambient transcripts (TV, conversation) get dispatched to
            # the LLM mid-research and Valet tries to "answer" them — which
            # was the source of every "got lost during research" report.
            # Only the cancel-word allowlist passes through; everything else
            # is logged and dropped silently.
            if active_research_task is not None and not active_research_task.done():
                if _is_cancel_phrase(user_text):
                    log.info(f"Research cancel triggered by transcript: {user_text!r}")
                    active_research_task.cancel()
                    try:
                        await asyncio.wait_for(active_research_task, timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception as e:
                        log.warning(f"research cancel cleanup failed: {e}")
                    active_research_task = None
                    try:
                        cancel_msg = "Cancelled, sir."
                        cancel_audio = await synthesize_speech(cancel_msg)
                        if cancel_audio:
                            await ws.send_json({"type": "status", "state": "speaking"})
                            await ws.send_json({
                                "type": "audio",
                                "data": base64.b64encode(cancel_audio).decode(),
                                "text": cancel_msg,
                            })
                        await ws.send_json({"type": "status", "state": "idle"})
                        log.info(f"VALET: {cancel_msg}")
                    except Exception as e:
                        log.warning(f"cancel-speech failed: {e}")
                    continue
                # Not a cancel phrase — log and drop. STT keeps running so
                # the user can still issue a cancel, but no LLM/action call
                # fires for this transcript.
                log.info(f"Suppressed during research (no cancel keyword): {user_text!r}")
                try:
                    await ws.send_json({"type": "status", "state": "idle"})
                except Exception:
                    pass
                continue

            await ws.send_json({"type": "status", "state": "thinking"})

            # If a pending alias offer is waiting (register-on-miss or remove-
            # stale-alias), give it first crack at this utterance. Cleared
            # whether handled or not — if the user moved on, drop the offer.
            if getattr(ws, "pending_offer", None) is not None:
                handled = await _handle_pending_offer(user_text, ws)
                ws.pending_offer = None
                if handled:
                    history.append({"role": "user", "content": user_text})
                    history.append({"role": "assistant", "content": "(offer handled)"})
                    await ws.send_json({"type": "status", "state": "idle"})
                    continue

            # Lazy project scan on first message
            global cached_projects
            if not cached_projects:
                try:
                    # Run in executor since scan_projects does sync file I/O
                    loop = asyncio.get_event_loop()
                    cached_projects = await asyncio.wait_for(
                        loop.run_in_executor(None, _scan_projects_sync),
                        timeout=3
                    )
                    log.info(f"Scanned {len(cached_projects)} projects")
                except Exception:
                    cached_projects = []

            try:
                # ── CHECK FOR MODE SWITCHES ──
                t_lower = user_text.lower()

                # ── PLANNING MODE: answering clarifying questions ──
                if planner.is_planning:
                    # Check for bypass
                    if any(p in t_lower for p in BYPASS_PHRASES):
                        plan = planner.active_plan
                        if plan:
                            plan.skipped = True
                            for q in plan.pending_questions[plan.current_question_index:]:
                                if q.get("default") is not None and q["key"] not in plan.answers:
                                    plan.answers[q["key"]] = q["default"]
                        prompt = await planner.build_prompt()
                        name = _generate_project_name(prompt)
                        path = str(Path.home() / "Desktop" / name)
                        os.makedirs(path, exist_ok=True)
                        Path(path, "CLAUDE.md").write_text(prompt)
                        did = dispatch_registry.register(name, path, prompt[:200])
                        asyncio.create_task(_execute_prompt_project(name, prompt, work_session, ws, dispatch_id=did, history=history, voice_state=voice_state))
                        planner.reset()
                        response_text = "Building it now, sir."
                    elif planner.active_plan and planner.active_plan.confirmed is False and planner.active_plan.current_question_index >= len(planner.active_plan.pending_questions):
                        # Confirmation phase
                        result = await planner.handle_confirmation(user_text)
                        if result["confirmed"]:
                            prompt = await planner.build_prompt()
                            name = _generate_project_name(prompt)
                            path = str(Path.home() / "Desktop" / name)
                            os.makedirs(path, exist_ok=True)
                            Path(path, "CLAUDE.md").write_text(prompt)
                            did = dispatch_registry.register(name, path, prompt[:200])
                            asyncio.create_task(_execute_prompt_project(name, prompt, work_session, ws, dispatch_id=did, history=history, voice_state=voice_state))
                            planner.reset()
                            response_text = "On it, sir."
                        elif result["cancelled"]:
                            planner.reset()
                            response_text = "Cancelled, sir."
                        else:
                            response_text = result.get("modification_question", "How shall I adjust the plan, sir?")
                    else:
                        result = await planner.process_answer(user_text, cached_projects)
                        if result["plan_complete"]:
                            response_text = result.get("confirmation_summary", "Ready to build. Shall I proceed, sir?")
                        else:
                            response_text = result.get("next_question", "What else, sir?")

                elif any(w in t_lower for w in ["quit work mode", "exit work mode", "go back to chat", "regular mode", "stop working"]):
                    if work_session.active:
                        await work_session.stop()
                        response_text = "Back to conversation mode, sir."
                    else:
                        response_text = "Already in conversation mode, sir."

                # ── WORK MODE: speech → claude -p → Haiku summary → VALET voice ──
                elif work_session.active:
                    if is_casual_question(user_text):
                        # Quick chat — bypass claude -p, use Haiku
                        response_text = await generate_response(
                            user_text, anthropic_client, task_manager,
                            cached_projects, history,
                            last_response=last_valet_response,
                            session_summary=session_summary,
                        )
                    else:
                        # Send to claude -p (full power)
                        await ws.send_json({"type": "status", "state": "working"})
                        log.info(f"Work mode → claude -p: {user_text[:80]}")

                        full_response = await work_session.send(user_text)

                        # Detect if Claude Code is stalling (asking questions instead of building)
                        if full_response and anthropic_client:
                            stall_words = ["which option", "would you prefer", "would you like me to",
                                           "before I proceed", "before proceeding", "should I",
                                           "do you want me to", "let me know", "please confirm",
                                           "which approach", "what would you"]
                            is_stalling = any(w in full_response.lower() for w in stall_words)
                            if is_stalling and work_session._message_count >= 2:
                                # Claude Code keeps asking — push it to build
                                log.info("Claude Code stalling — pushing to build")
                                push_response = await work_session.send(
                                    "Stop asking questions. Use your best judgment and start building now. "
                                    "Write the actual code files. Go with the simplest reasonable approach."
                                )
                                if push_response:
                                    full_response = push_response

                        # Auto-open any localhost URLs Claude Code mentions
                        import re as _re
                        localhost_match = _re.search(r'https?://localhost:\d+', full_response or "")
                        if localhost_match:
                            asyncio.create_task(_execute_browse(localhost_match.group(0)))
                            log.info(f"Auto-opening {localhost_match.group(0)}")

                        # Always summarize work mode responses via Haiku
                        if full_response and anthropic_client:
                            try:
                                summary = await anthropic_client.messages.create(
                                    model="claude-haiku-4-5-20251001",
                                    max_tokens=100,
                                    system=(
                                        f"You are VALET reporting to the user ({USER_NAME}). Summarize what happened in 1-2 sentences. "
                                        "Speak in first person — 'I built', 'I found', 'I set up'. "
                                        "You are talking TO THE USER, not to a coding tool. "
                                        "NEVER give instructions like 'go ahead and build' or 'set up the frontend' — those are NOT for the user. "
                                        "NEVER say 'Claude Code'. NEVER output [ACTION:...] tags. "
                                        "NEVER read out URLs. No markdown. British precision."
                                    ),
                                    messages=[{"role": "user", "content": f"Claude Code said:\n{full_response[:2000]}"}],
                                )
                                response_text = summary.content[0].text
                            except Exception:
                                response_text = full_response[:200]
                        else:
                            response_text = full_response

                # ── CHAT MODE: fast keyword detection + Haiku ──
                else:
                    action = detect_action_fast(user_text, ws=ws)

                    # close_panel is handled silently before any TTS — VALET
                    # just dismisses the panel without speaking.
                    if action and action["action"] == "close_panel":
                        try:
                            await ws.send_json({"type": "close_panel"})
                            await ws.send_json({"type": "status", "state": "idle"})
                        except Exception:
                            pass
                        history.append({"role": "user", "content": user_text})
                        history.append({"role": "assistant", "content": "(panel dismissed)"})
                        continue

                    if action:
                        _track_usage(action.get("action") or "")
                        if action["action"] == "open_terminal":
                            response_text = await handle_open_terminal()
                        elif action["action"] == "show_recent":
                            response_text = await handle_show_recent()
                        elif action["action"] == "describe_screen":
                            response_text = "Taking a look now, sir."
                            asyncio.create_task(_lookup_and_report("screen", _do_screen_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "ui_act":
                            # UC3 — "click on X" / "type X into the Y field". Resolve +
                            # gated execute on a background task (keeps the WS loop free
                            # for the confirm reply).
                            response_text = "On it, sir."
                            asyncio.create_task(_handle_ui_act(action, ws))
                        elif action["action"] == "check_calendar":
                            response_text = "Checking your calendar now, sir."
                            asyncio.create_task(_lookup_and_report("calendar", _do_calendar_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_mail":
                            response_text = "Checking your inbox now, sir."
                            asyncio.create_task(_lookup_and_report("mail", _do_mail_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_weather":
                            wx_target = (action.get("target") or "").strip()
                            wx_when = action.get("when") or "today"
                            response_text = (
                                "Checking the weather now, sir." if not wx_target
                                else f"Checking the weather in {wx_target}, sir."
                            )
                            # Wrap in a lambda since _do_weather_lookup takes (location, ws);
                            # _lookup_and_report expects a zero-arg coroutine factory.
                            asyncio.create_task(_lookup_and_report(
                                "weather",
                                lambda t=wx_target, w=wx_when: _do_weather_lookup(t, ws, w),
                                ws, history=history, voice_state=voice_state,
                            ))
                        elif action["action"] == "check_dispatch":
                            recent = dispatch_registry.get_most_recent()
                            if not recent:
                                response_text = "No recent builds on record, sir."
                            else:
                                name = recent["project_name"]
                                status = recent["status"]
                                if status == "building" or status == "pending":
                                    elapsed = int(time.time() - recent["updated_at"])
                                    response_text = f"Still working on {name}, sir. Been at it for {elapsed} seconds."
                                elif status == "completed":
                                    response_text = recent.get("summary") or f"{name} is complete, sir."
                                elif status in ("failed", "timeout"):
                                    response_text = f"{name} ran into problems, sir."
                                else:
                                    response_text = f"{name} is {status}, sir."
                        elif action["action"] == "check_tasks":
                            tasks = get_open_tasks()
                            response_text = format_tasks_for_voice(tasks)
                        elif action["action"] == "list_projects":
                            response_text = _format_projects_for_voice(list_projects())
                        elif action["action"] == "open_app":
                            target = action.get("target", "").strip()
                            response_text = f"Opening {target}, sir." if target else "Opening that now, sir."
                            if target:
                                asyncio.create_task(_execute_open_app(target))
                        elif action["action"] == "open_url":
                            url = action.get("target", "").strip()
                            label = (action.get("label") or "").strip() or "that"
                            browser = action.get("browser", "chrome")
                            response_text = f"Opening {label}, sir."
                            if url:
                                asyncio.create_task(_execute_open_url(url, browser, label))
                        elif action["action"] == "open_note":
                            note_q = action.get("target", "").strip()
                            response_text = f"Opening your {note_q} note, sir." if note_q else "Which note, sir?"
                            if note_q:
                                asyncio.create_task(_execute_open_note(note_q, ws))
                        elif action["action"] == "open_project":
                            target = action.get("target", "").strip()
                            response_text = f"Opening {target}, sir." if target else "Which project, sir?"
                            if target:
                                asyncio.create_task(_execute_open_project(target, ws))
                        elif action["action"] == "register_project":
                            raw_path = action.get("target", "")
                            alias = action.get("alias", "") or None
                            async with process_bus.task_context(f"Register: {raw_path}") as _rt:
                                result = await register_project(raw_path, alias, task_id=_rt)
                            response_text = result.get("confirmation", "Done, sir.")
                        elif action["action"] == "refresh_context":
                            response_text = "Refreshing context, sir."

                            async def _refresh_silent():
                                try:
                                    import project_context
                                    await project_context.refresh()
                                except Exception as exc:
                                    log.warning(f"silent refresh_context failed: {exc}")

                            asyncio.create_task(_refresh_silent())
                        elif action["action"] == "start_design":
                            response_text = ""  # _execute_start_design speaks
                            topic = action.get("target", "")
                            asyncio.create_task(_execute_start_design(
                                topic, ws, new_project=bool(action.get("new_project")),
                            ))
                        elif action["action"] == "start_dictation":
                            response_text = ""  # _execute_start_dictation speaks
                            asyncio.create_task(_execute_start_dictation(ws))
                        elif action["action"] == "ship_design":
                            response_text = ""
                            asyncio.create_task(_execute_ship_design(ws))
                        elif action["action"] == "dispatch_to_agent":
                            response_text = ""
                            asyncio.create_task(_execute_dispatch_to_agent(
                                ws,
                                action.get("agent", ""),
                                action.get("task", ""),
                            ))
                        elif action["action"] == "scrap_design":
                            response_text = ""
                            asyncio.create_task(_execute_scrap_design(ws))
                        elif action["action"] == "show_draft":
                            response_text = ""
                            asyncio.create_task(_execute_show_draft(ws))
                        elif action["action"] == "merge_branch":
                            response_text = ""
                            asyncio.create_task(_execute_merge_branch(ws))
                        elif action["action"] == "restart_self":
                            response_text = ""
                            asyncio.create_task(_execute_restart_self(ws))
                        elif action["action"] == "check_usage":
                            response_text = get_usage_summary()
                        else:
                            response_text = "Understood, sir."
                    else:
                        # ── DESIGNING branch — bypass Haiku entirely while a
                        # design session is active and in DESIGNING state.
                        # Routes the turn through Opus + design_turn tool-use.
                        import design_partner
                        active_session = design_partner.get_for_ws(ws)
                        if active_session is not None and active_session.state == "DESIGNING":
                            if not anthropic_client:
                                response_text = "API key not configured, sir."
                            else:
                                try:
                                    voice_reply = await active_session.handle_turn(user_text, anthropic_client)
                                    await _speak(ws, voice_reply)
                                    response_text = ""  # speak already happened
                                except Exception as e:
                                    log.error(f"design handle_turn failed: {e}", exc_info=True)
                                    response_text = "I had trouble thinking that through, sir."
                        elif not anthropic_client:
                            response_text = "API key not configured."
                        else:
                            response_text = await generate_response(
                                user_text, anthropic_client, task_manager,
                                cached_projects, history,
                                last_response=last_valet_response,
                                session_summary=session_summary,
                            )

                            # Check for action tags embedded in LLM response
                            clean_response, embedded_action = extract_action(response_text)
                            if embedded_action:
                                log.info(f"LLM embedded action: {embedded_action}")
                                response_text = clean_response
                                # Ensure there's always something to speak
                                if not response_text.strip():
                                    action_type = embedded_action["action"]
                                    if action_type == "prompt_project":
                                        proj = embedded_action["target"].split("|||")[0].strip()
                                        response_text = f"Connecting to {proj} now, sir."
                                    elif action_type == "build":
                                        response_text = "On it, sir."
                                    elif action_type == "research":
                                        response_text = "Looking into that now, sir."
                                    else:
                                        response_text = "Right away, sir."

                                # Phase H: global kill switch halts ALL actions;
                                # Tier 1 actions outside the gated executor confirm here.
                                if kill_switch.is_engaged():
                                    await _speak(ws, "Halted, sir.")
                                elif embedded_action["action"] in _CONFIRM_ACTIONS:
                                    # Confirm + run on a background task. Awaiting the
                                    # confirmation here would deadlock the receive loop
                                    # against its own confirm_response reply (see
                                    # _confirm_and_dispatch). create_event / cancel_event
                                    # / send are dispatched there, NOT in the branches
                                    # below.
                                    asyncio.create_task(_confirm_and_dispatch(ws, embedded_action))
                                elif embedded_action["action"] == "build":
                                    # Build in background — VALET stays conversational
                                    target = embedded_action["target"]
                                    name = _generate_project_name(target)
                                    path = str(Path.home() / "Desktop" / name)
                                    os.makedirs(path, exist_ok=True)

                                    # Write detailed CLAUDE.md
                                    Path(path, "CLAUDE.md").write_text(
                                        f"# Task\n\n{target}\n\n"
                                        "## Instructions\n"
                                        "- BUILD THIS NOW. Do not ask clarifying questions.\n"
                                        "- Use your best judgment for any design/architecture decisions.\n"
                                        "- Write complete, working code files — not plans or specs.\n"
                                        "- If it's a web app: use React + Vite + Tailwind unless specified otherwise.\n"
                                        "- Make it look polished and professional. Modern UI, clean layout.\n"
                                        "- Ensure it runs with a single command (npm run dev or similar).\n"
                                        "- If you reference a real product's UI (e.g. 'Zillow clone'), match their actual layout and features closely.\n"
                                        "- Use realistic mock data, not placeholder Lorem Ipsum.\n"
                                        "- After building, start the dev server and verify the app loads without errors.\n"
                                        "- IMPORTANT: Your LAST line of output MUST be exactly: RUNNING_AT=http://localhost:PORT (the actual port the dev server is using)\n"
                                    )

                                    # Register and dispatch
                                    did = dispatch_registry.register(name, path, target)
                                    asyncio.create_task(
                                        _execute_prompt_project(name, target, work_session, ws, dispatch_id=did, history=history, voice_state=voice_state)
                                    )
                                elif embedded_action["action"] == "browse":
                                    asyncio.create_task(_execute_browse(embedded_action["target"]))
                                elif embedded_action["action"] == "research":
                                    # Native research — Opus 4.7 + server-side
                                    # web_search/web_fetch tools. No folder, no
                                    # subprocess, no Cursor handoff. Results
                                    # render as cards in the Process Panel and
                                    # are spoken as a short summary via TTS.
                                    # See docs/research_routing_diagnosis.md.
                                    log.info("research dispatch: routing to native handler (target=%r)",
                                             embedded_action["target"][:160])
                                    # Capture the task handle so the transcript
                                    # intercept above can (a) suppress ambient
                                    # transcripts during the long-running call
                                    # and (b) honor cancel/stop/nevermind to
                                    # abort it cleanly.
                                    active_research_task = asyncio.create_task(
                                        _execute_native_research(embedded_action["target"], ws)
                                    )
                                elif embedded_action["action"] == "open_terminal":
                                    asyncio.create_task(_execute_open_terminal())
                                elif embedded_action["action"] == "open_app":
                                    asyncio.create_task(_execute_open_app(embedded_action["target"]))
                                elif embedded_action["action"] == "new_project":
                                    asyncio.create_task(_execute_new_project(embedded_action["target"], ws))
                                elif embedded_action["action"] == "open_project":
                                    asyncio.create_task(_execute_open_project(embedded_action["target"], ws))
                                elif embedded_action["action"] == "list_projects":
                                    # LLM fallback when fast-path missed — speak the authoritative list.
                                    response_text = _format_projects_for_voice(list_projects())
                                elif embedded_action["action"] == "refresh_context":
                                    asyncio.create_task(_execute_refresh_context(embedded_action["target"], ws))
                                elif embedded_action["action"] == "start_design":
                                    asyncio.create_task(_execute_start_design(embedded_action["target"], ws))
                                elif embedded_action["action"] == "start_dictation":
                                    asyncio.create_task(_execute_start_dictation(ws))
                                elif embedded_action["action"] == "ship_design":
                                    asyncio.create_task(_execute_ship_design(ws))
                                elif embedded_action["action"] == "scrap_design":
                                    asyncio.create_task(_execute_scrap_design(ws))
                                elif embedded_action["action"] == "show_draft":
                                    asyncio.create_task(_execute_show_draft(ws))
                                elif embedded_action["action"] == "merge_branch":
                                    asyncio.create_task(_execute_merge_branch(ws))
                                elif embedded_action["action"] == "restart_self":
                                    asyncio.create_task(_execute_restart_self(ws))
                                elif embedded_action["action"] == "delete_file":
                                    # Tier 1: confirm-first (to Trash) via the safety gate.
                                    asyncio.create_task(_run_gated_action(ws, executor.delete_file(embedded_action["target"])))
                                elif embedded_action["action"] == "write_file":
                                    # "path ||| contents". Tier 1 when overwriting (gated in executor).
                                    _wp, _, _wc = embedded_action["target"].partition("|||")
                                    asyncio.create_task(_run_gated_action(ws, executor.write_file(_wp.strip(), _wc.strip())))
                                elif embedded_action["action"] == "move_file":
                                    # "src ||| dst". Tier 1 (gated in executor).
                                    _ms, _, _md = embedded_action["target"].partition("|||")
                                    asyncio.create_task(_run_gated_action(ws, executor.move_file(_ms.strip(), _md.strip())))
                                elif embedded_action["action"] == "list_folder":
                                    # Tier 0: read-only listing.
                                    asyncio.create_task(_run_gated_action(ws, executor.list_folder(embedded_action["target"])))
                                elif embedded_action["action"] == "applescript":
                                    # Tier 1: arbitrary AppleScript confirms first.
                                    asyncio.create_task(_run_gated_action(ws, executor.run_script(embedded_action["target"])))
                                elif embedded_action["action"] == "type":
                                    asyncio.create_task(_execute_type(embedded_action["target"], press_enter=False))
                                # NOTE: "send", "create_event" and "cancel_event" are in
                                # _CONFIRM_ACTIONS and dispatched via _confirm_and_dispatch
                                # above — no branch for them here.
                                elif embedded_action["action"] == "dispatch_to_agent":
                                    # LLM-emitted dispatch: target is "<agent> ||| <task>".
                                    raw = embedded_action.get("target", "")
                                    if "|||" in raw:
                                        agent_name, _, task = raw.partition("|||")
                                        asyncio.create_task(_execute_dispatch_to_agent(
                                            ws, agent_name.strip(), task.strip(),
                                        ))
                                    else:
                                        log.warning(f"dispatch_to_agent missing |||: {raw[:120]!r}")
                                elif embedded_action["action"] == "check_date":
                                    asyncio.create_task(_execute_check_date(embedded_action["target"], ws))
                                elif embedded_action["action"] == "check_weather":
                                    wx_t = (embedded_action.get("target") or "").strip()
                                    # LLM tag carries no time scope — recover it
                                    # from the original utterance ("for tomorrow").
                                    wx_w = _weather_when(user_text)
                                    asyncio.create_task(_lookup_and_report(
                                        "weather",
                                        lambda t=wx_t, w=wx_w: _do_weather_lookup(t, ws, w),
                                        ws, history=history, voice_state=voice_state,
                                    ))
                                elif embedded_action["action"] == "draft_email":
                                    asyncio.create_task(_execute_draft_email(embedded_action["target"], ws))
                                elif embedded_action["action"] == "save_contact":
                                    asyncio.create_task(_execute_save_contact(embedded_action["target"], ws))
                                elif embedded_action["action"] == "prompt_project":
                                    target = embedded_action["target"]
                                    if "|||" in target:
                                        proj_name, _, prompt = target.partition("|||")
                                        proj_name = proj_name.strip()
                                        prompt = prompt.strip()
                                        # Check for recent completed dispatch before re-dispatching
                                        recent = dispatch_registry.get_recent_for_project(proj_name)
                                        if recent and recent.get("summary"):
                                            log.info(f"Using recent dispatch result for {proj_name} instead of re-dispatching")
                                            response_text = recent["summary"]
                                            history.append({"role": "assistant", "content": f"[Previous dispatch result for {proj_name}]: {recent['summary']}"})
                                        else:
                                            asyncio.create_task(
                                                _execute_prompt_project(proj_name, prompt, work_session, ws, history=history, voice_state=voice_state)
                                            )
                                    else:
                                        log.warning(f"PROMPT_PROJECT missing ||| delimiter: {target}")
                                elif embedded_action["action"] == "add_task":
                                    target = embedded_action["target"]
                                    parts = target.split("|||")
                                    if len(parts) >= 2:
                                        priority = parts[0].strip() or "medium"
                                        title = parts[1].strip()
                                        desc = parts[2].strip() if len(parts) > 2 else ""
                                        due = parts[3].strip() if len(parts) > 3 else ""
                                        create_task(title=title, description=desc, priority=priority, due_date=due)
                                        log.info(f"Task created: {title}")
                                elif embedded_action["action"] == "add_note":
                                    target = embedded_action["target"]
                                    if "|||" in target:
                                        topic, _, content = target.partition("|||")
                                        create_note(content=content.strip(), topic=topic.strip())
                                    else:
                                        create_note(content=target)
                                    log.info(f"Note created")
                                elif embedded_action["action"] == "complete_task":
                                    try:
                                        task_id = int(embedded_action["target"].strip())
                                        complete_task(task_id)
                                        log.info(f"Task {task_id} completed")
                                    except ValueError:
                                        pass
                                elif embedded_action["action"] == "remember":
                                    remember(embedded_action["target"].strip(), mem_type="fact", importance=7)
                                    log.info(f"Memory stored: {embedded_action['target'][:60]}")
                                elif embedded_action["action"] == "bio_add":
                                    add_bio_note(embedded_action["target"].strip())
                                    log.info(f"Bio note added: {embedded_action['target'][:60]}")
                                elif embedded_action["action"] == "create_note":
                                    target = embedded_action["target"]
                                    if "|||" in target:
                                        title, _, body = target.partition("|||")
                                        asyncio.create_task(create_apple_note(title.strip(), body.strip()))
                                        log.info(f"Apple Note created: {title.strip()}")
                                    else:
                                        asyncio.create_task(create_apple_note("VALET Note", target))
                                elif embedded_action["action"] == "screen":
                                    asyncio.create_task(_lookup_and_report("screen", _do_screen_lookup, ws, history=history, voice_state=voice_state))
                                elif embedded_action["action"] == "read_note":
                                    # Read note in background and report back
                                    async def _read_and_report(search_term, _ws):
                                        note = await read_note(search_term)
                                        if note:
                                            msg = f"Sir, your note '{note['title']}' says: {note['body'][:200]}"
                                        else:
                                            msg = f"Couldn't find a note matching '{search_term}', sir."
                                        audio = await synthesize_speech(strip_markdown_for_tts(msg))
                                        if audio and _ws:
                                            try:
                                                await _ws.send_json({"type": "status", "state": "speaking"})
                                                await _ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                                            except Exception:
                                                pass
                                    asyncio.create_task(_read_and_report(embedded_action["target"].strip(), ws))

                # Update history
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": response_text})

                # Three-tier memory: also track in session buffer
                session_buffer.append({"role": "user", "content": user_text})
                session_buffer.append({"role": "assistant", "content": response_text})

                # Check if rolling summary needs updating
                messages_since_last_summary += 1
                if messages_since_last_summary >= 5 and len(history) > 20 and not summary_update_pending:
                    summary_update_pending = True
                    messages_since_last_summary = 0
                    # Get messages that are about to be rotated out
                    rotated = history[:-20] if len(history) > 20 else []
                    if rotated and anthropic_client:
                        async def _do_summary():
                            nonlocal session_summary, summary_update_pending
                            session_summary = await _update_session_summary(
                                session_summary, rotated, anthropic_client
                            )
                            summary_update_pending = False
                        asyncio.create_task(_do_summary())
                    else:
                        summary_update_pending = False

                # Extract memories in background (doesn't block response)
                if anthropic_client and len(user_text) > 15:
                    asyncio.create_task(extract_memories(user_text, response_text, anthropic_client))

                # TTS — skip entirely if response_text is empty (handler did its
                # own _speak() call, e.g. design-partner branch). Em-dashes are
                # stripped from BOTH the TTS input (via strip_markdown_for_tts)
                # and the on-screen caption text so neither read like an LLM
                # transcript.
                if response_text:
                    response_text = strip_em_dashes(response_text)
                    tts = strip_markdown_for_tts(response_text)
                    await ws.send_json({"type": "status", "state": "speaking"})
                    # Sentence-chunked so a multi-sentence reply starts speaking
                    # before the whole thing is synthesized.
                    if not await _speak_chunks(ws, tts, response_text):
                        await ws.send_json({"type": "text", "text": response_text})
                        await ws.send_json({"type": "status", "state": "idle"})
                    log.info(f"VALET: {response_text}")
                    last_valet_response = response_text
                else:
                    await ws.send_json({"type": "status", "state": "idle"})

            except Exception as e:
                log.error(f"Error: {e}", exc_info=True)
                try:
                    fallback = "Something went wrong, sir."
                    audio = await synthesize_speech(fallback)
                    if audio:
                        await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": fallback})
                    else:
                        await ws.send_json({"type": "audio", "data": "", "text": fallback})
                    # Let client's audioPlayer.onFinished handle idle transition
                except Exception:
                    pass

    except WebSocketDisconnect:
        log.info("Voice WebSocket disconnected")
    except Exception as e:
        log.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        task_manager.unregister_websocket(ws)
        await process_bus.unsubscribe(ws)
        _obs_scope.close()


# ---------------------------------------------------------------------------
# Settings / Configuration endpoints
# ---------------------------------------------------------------------------

def _env_file_path() -> Path:
    # Writable user .env in a packaged build, repo .env in dev (see valet_env_path).
    return valet_env_path()

def _env_example_path() -> Path:
    return Path(__file__).parent / ".env.example"

def _read_env() -> tuple[list[str], dict[str, str]]:
    """Read .env file. Returns (raw_lines, parsed_dict). Creates from .env.example if missing."""
    path = _env_file_path()
    if not path.exists():
        example = _env_example_path()
        if example.exists():
            import shutil as _shutil
            _shutil.copy2(str(example), str(path))
        else:
            path.write_text("")
    lines = path.read_text().splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, v = stripped.partition("=")
            parsed[k.strip()] = v.strip().strip('"').strip("'")
    return lines, parsed

def _write_env_key(key: str, value: str) -> None:
    """Update a single key in .env, preserving comments and order."""
    lines, _ = _read_env()
    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key:
                new_lines.append(f"{key}={value}")
                found = True
                continue
        new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    _env_file_path().write_text("\n".join(new_lines) + "\n")
    os.environ[key] = value

class KeyUpdate(BaseModel):
    key_name: str
    key_value: str

class KeyTest(BaseModel):
    key_value: str | None = None

class PreferencesUpdate(BaseModel):
    user_name: str = ""
    honorific: str = "sir"
    calendar_accounts: str = "auto"
    date_of_birth: str = ""
    address: str = ""
    hometown_city: str = ""

@app.post("/api/settings/keys")
async def api_settings_keys(body: KeyUpdate):
    # The app holds NO vendor secrets — only its license key and the proxy URL.
    allowed = {"LICENSE_KEY", "PROXY_BASE_URL", "FISH_VOICE_ID", "VALET_VOICE", "VALET_VOICE_MALE_ID", "VALET_VOICE_FEMALE_ID", "VALET_TELEMETRY", "USER_NAME", "HONORIFIC", "CALENDAR_ACCOUNTS", "DATE_OF_BIRTH", "ADDRESS", "HOMETOWN_CITY", "WORK_EMAIL", "PERSONAL_EMAIL"}
    if body.key_name not in allowed:
        return JSONResponse({"success": False, "error": "Invalid key name"}, status_code=400)
    _write_env_key(body.key_name, body.key_value)
    return {"success": True}

@app.post("/api/settings/test-license")
async def api_test_license(body: KeyTest):
    """Validate the license against the proxy. Replaces the old per-vendor key
    tests — the app no longer holds Anthropic/Fish keys."""
    import licensing
    key = (body.key_value or os.getenv("LICENSE_KEY", "")).strip()
    base = (os.getenv("PROXY_BASE_URL", "") or PROXY_BASE_URL).rstrip("/")
    if not key:
        return {"valid": False, "error": "No license key provided"}
    state = await licensing.validate(key, base)
    status = state.get("status", "invalid")
    if status in licensing.ENTITLED:
        return {"valid": True, "status": status}
    if status == "offline":
        ok = licensing.is_entitled()
        return {"valid": ok, "status": "offline",
                "error": None if ok else "Cannot reach the licensing server."}
    return {"valid": False, "status": status, "error": f"License is {status}."}

@app.get("/api/settings/status")
async def api_settings_status():
    import shutil as _shutil
    _, env_dict = _read_env()
    claude_installed = _shutil.which("claude") is not None
    # Google connectivity drives both calendar + mail status. Apple Notes still
    # uses AppleScript and is independent.
    google_connected = google_auth.is_connected()
    google_email = google_auth.get_connected_email() if google_connected else None
    google_creds_present = google_auth.credentials_file_exists()
    notes_ok = False
    try: await get_recent_notes(count=1); notes_ok = True
    except Exception: pass
    memory_count = task_count = 0
    try: memory_count = len(get_important_memories(limit=9999))
    except Exception: pass
    try: task_count = len(get_open_tasks())
    except Exception: pass
    return {
        "claude_code_installed": claude_installed,
        "calendar_accessible": google_connected,
        "mail_accessible": google_connected,
        "notes_accessible": notes_ok,
        "google_connected": google_connected,
        "google_email": google_email or "",
        "google_credentials_present": google_creds_present,
        "memory_count": memory_count,
        "task_count": task_count,
        "server_port": 8340,
        "uptime_seconds": int(time.time() - _session_start),
        "env_keys_set": {
            "license": bool(env_dict.get("LICENSE_KEY", "").strip()),
            "proxy_base_url": (env_dict.get("PROXY_BASE_URL", "").strip() or PROXY_BASE_URL),
            "license_status": _license_status_label(),
            "fish_voice_id": bool(env_dict.get("FISH_VOICE_ID", "").strip()),
            "user_name": env_dict.get("USER_NAME", ""),
        },
    }

@app.get("/api/settings/preferences")
async def api_get_preferences():
    _, env_dict = _read_env()
    bio = get_bio_summary()
    sources = get_bio_sources()
    return {
        "user_name": env_dict.get("USER_NAME", ""),
        "honorific": env_dict.get("HONORIFIC", "sir"),
        "calendar_accounts": env_dict.get("CALENDAR_ACCOUNTS", "auto"),
        "date_of_birth": env_dict.get("DATE_OF_BIRTH", ""),
        "address": env_dict.get("ADDRESS", ""),
        "hometown_city": env_dict.get("HOMETOWN_CITY", ""),
        "bio_summary": bio["summary"],
        "bio_summary_updated": bio["updated"],
        "bio_source_count": len(sources),
    }

@app.post("/api/settings/preferences")
async def api_save_preferences(body: PreferencesUpdate):
    _write_env_key("USER_NAME", body.user_name)
    _write_env_key("HONORIFIC", body.honorific)
    _write_env_key("CALENDAR_ACCOUNTS", body.calendar_accounts)
    _write_env_key("DATE_OF_BIRTH", body.date_of_birth)
    _write_env_key("ADDRESS", body.address)
    _write_env_key("HOMETOWN_CITY", body.hometown_city)
    return {"success": True}

@app.get("/api/google/status")
async def api_google_status():
    connected = google_auth.is_connected()
    return {
        "connected": connected,
        "email": (google_auth.get_connected_email() if connected else "") or "",
        "credentials_present": google_auth.credentials_file_exists(),
    }


@app.post("/api/google/connect")
async def api_google_connect():
    """Kick off the OAuth flow. Blocks the request until the user finishes consent
    in the browser. The flow opens a localhost-callback page automatically.
    """
    if not google_auth.credentials_file_exists():
        return JSONResponse({
            "success": False,
            "error": "google_credentials.json missing — download OAuth client JSON from Google Cloud Console (Desktop app type) and place it at the project root.",
        }, status_code=400)
    try:
        # Generous timeout: user has 5 minutes to complete consent.
        success, message = await asyncio.wait_for(google_auth.connect_async(), timeout=300)
    except asyncio.TimeoutError:
        return JSONResponse({"success": False, "error": "Consent flow timed out after 5 minutes"}, status_code=504)
    if not success:
        return JSONResponse({"success": False, "error": message}, status_code=500)
    return {"success": True, "email": message}


@app.post("/api/google/disconnect")
async def api_google_disconnect():
    google_auth.disconnect()
    return {"success": True}


@app.get("/api/contacts")
async def api_contacts_list():
    """The user's saved contacts (profile store) for the settings address book."""
    return {"contacts": list_contacts()}


@app.post("/api/contacts")
async def api_contacts_add(request: Request):
    """Add or update a saved contact. Body: {name, email}."""
    body = await request.json()
    name = (body or {}).get("name", "")
    email = (body or {}).get("email", "")
    if not (name or "").strip() or "@" not in (email or ""):
        return JSONResponse({"success": False, "error": "name and a valid email are required"}, status_code=400)
    ok = add_contact(name, email)
    return {"success": ok, "contacts": list_contacts()}


@app.delete("/api/contacts")
async def api_contacts_delete(request: Request):
    """Remove a saved contact by name. Body: {name}."""
    body = await request.json()
    name = (body or {}).get("name", "")
    ok = delete_contact(name)
    return {"success": ok, "contacts": list_contacts()}


@app.post("/api/settings/bio/regenerate")
async def api_regenerate_bio():
    """Synthesize a fresh user-profile summary from accumulated notes.

    VALET pulls bio_notes (voice-added) and high-importance facts, then asks
    Haiku to write a concise third-person profile. The result is persisted as
    type='bio_summary', importance=10 — so it flows into the system prompt
    via the existing get_important_memories() injection on every request.
    """
    if not anthropic_client:
        return JSONResponse({"success": False, "error": "ANTHROPIC_API_KEY not configured"}, status_code=400)

    sources = get_bio_sources()
    _, env_dict = _read_env()
    name = env_dict.get("USER_NAME", "").strip() or "the user"

    # Hard-coded structured identity from env, so the summary always knows the basics.
    identity_lines: list[str] = []
    if env_dict.get("DATE_OF_BIRTH", "").strip():
        identity_lines.append(f"Date of birth: {env_dict['DATE_OF_BIRTH'].strip()}")
    if env_dict.get("ADDRESS", "").strip():
        identity_lines.append(f"Address/location: {env_dict['ADDRESS'].strip()}")

    note_lines = [f"- [{s['type']}, importance {s['importance']}] {s['content']}" for s in sources]

    if not identity_lines and not note_lines:
        set_bio_summary("")
        return {"success": True, "summary": "", "source_count": 0,
                "message": "No notes available yet — VALET needs more conversations to write a profile."}

    body_parts = []
    if identity_lines:
        body_parts.append("STRUCTURED IDENTITY:\n" + "\n".join(identity_lines))
    if note_lines:
        body_parts.append("RAW NOTES (importance-ordered):\n" + "\n".join(note_lines[:80]))
    source_text = "\n\n".join(body_parts)

    try:
        result = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=(
                "You are VALET writing a private dossier on the user you serve. "
                "Synthesize a tight 3-5 sentence profile in third person, factual and concrete. "
                "Cover: who they are, how they operate, what matters to them, useful context for serving them well. "
                "Plain prose only — no bullet lists, no headers, no markdown. "
                "Address the user by name when natural. Omit anything not supported by the notes. "
                "If notes are thin, write only what's defensible from them; don't invent details."
            ),
            messages=[{
                "role": "user",
                "content": f"User's name: {name}\n\n{source_text}\n\nWrite the profile summary now.",
            }],
        )
        summary = (result.content[0].text or "").strip()
    except Exception as e:
        log.error(f"Bio regenerate failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    set_bio_summary(summary)
    updated = get_bio_summary()
    return {"success": True, "summary": summary, "updated": updated["updated"], "source_count": len(sources)}

@app.get("/api/config")
async def api_get_config():
    # Read .env fresh so renaming the assistant only needs a backend restart.
    _, env_dict = _read_env()
    name = env_dict.get("ASSISTANT_NAME", "").strip() or "vee"
    voice = (env_dict.get("VALET_VOICE", "").strip().lower() or "male")
    telemetry = env_dict.get("VALET_TELEMETRY", "on").strip().lower() not in ("0", "off", "false", "no")
    return {
        "assistant_name": name,
        "voice": "female" if voice == "female" else "male",
        "voice_female_available": bool(env_dict.get("VALET_VOICE_FEMALE_ID", "").strip()),
        "telemetry": telemetry,
        "build_id": _build_id(),
    }


def _build_id() -> str:
    """A stamp the onboarding keys off: it re-runs whenever this changes. Combines
    the build stamp (new on every release) with the installed app's creation time
    (new on every fresh install, even of the same build, since a drag-install
    copies the bundle). So onboarding runs on every new download AND every
    reinstall, even if the user previously installed and deleted it. Falls back to
    'dev' unpackaged."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    try:
        stamp = (base / "build_id.txt").read_text().strip() or "dev"
    except Exception:
        stamp = "dev"
    inst = _install_stamp()
    return f"{stamp}.{inst}" if inst else stamp


def _install_stamp() -> str:
    """Creation time of the installed .app bundle. A drag-install copies the
    bundle, giving the copy a fresh birth time, so this differs on every reinstall
    even when the build is identical. Empty when not running from a .app."""
    if not getattr(sys, "frozen", False):
        return ""
    try:
        # sys.executable = .../VALET.app/Contents/MacOS/valet-backend
        app = Path(sys.executable).resolve().parents[2]
        if app.suffix == ".app":
            return str(int(os.stat(app).st_birthtime))
    except Exception:
        pass
    return ""

# ---------------------------------------------------------------------------
# Safety: global kill switch (Stage D)
# ---------------------------------------------------------------------------

@app.post("/api/safety/kill")
async def api_safety_kill():
    """Engage the kill switch: halt in-progress actions and refuse new ones
    until reset. Always available."""
    kill_switch.engage()
    confirmations.cancel_all(allow=False)
    await task_manager._notify({"type": "kill_state", "engaged": True})
    return {"engaged": True}

@app.post("/api/safety/kill/reset")
async def api_safety_kill_reset():
    kill_switch.reset()
    await task_manager._notify({"type": "kill_state", "engaged": False})
    return {"engaged": False}

@app.get("/api/safety/status")
async def api_safety_status():
    return {"kill_engaged": kill_switch.is_engaged(), "executor": executor.name}

# ---------------------------------------------------------------------------
# Permissions (Stage F onboarding)
# ---------------------------------------------------------------------------

def _check_full_disk_access() -> bool:
    """Full Disk Access lets VALET read files anywhere. Probe a TCC-protected
    path: readable only when FDA is granted."""
    for probe in (
        Path.home() / "Library/Application Support/com.apple.TCC/TCC.db",
        Path.home() / "Library/Mail",
    ):
        try:
            if probe.is_dir():
                next(iter(os.scandir(probe)), None)
                return True
            with open(probe, "rb") as f:
                f.read(1)
            return True
        except (PermissionError, OSError):
            continue
        except Exception:
            continue
    return False

def _calendar_access_granted():
    """Silent EventKit calendar check (no prompt). True if full access (read+
    create), False if denied/restricted, None if not-yet-asked or write-only."""
    try:
        import apple_calendar
        s = apple_calendar.auth_status()
        if s == 3:
            return True
        if s in (1, 2):
            return False
        return None  # 0 notDetermined or 4 write-only → still needs full access
    except Exception:
        return None


def _contacts_access_granted():
    """Silent Contacts check (no prompt). True authorized, False denied/restricted,
    None not-yet-asked. Contacts is an optional fallback for name→email lookup —
    VALET's own saved contacts work without it."""
    try:
        s = contacts_access.auth_status()
        if s == 3:
            return True
        if s in (1, 2):
            return False
        return None
    except Exception:
        return None


def _accessibility_access_granted():
    """Real TCC check via AXIsProcessTrusted (no prompt). True/False reflects the
    live grant for THIS process — so a revoke flips it straight back to False.
    None only if the AX backend can't be imported on this host."""
    try:
        import accessibility_executor
        return bool(accessibility_executor.is_trusted())
    except Exception:
        return None


def _screen_recording_granted():
    """Real TCC check via CGPreflightScreenCaptureAccess (no prompt). True/False
    for the live grant; None if the perception backend can't be imported."""
    try:
        import perception
        return bool(perception.screen_recording_trusted())
    except Exception:
        return None


@app.get("/api/permissions/status")
async def api_permissions_status():
    """First-run onboarding reads this to show what's granted and what to enable.
    Automation prompts per-app on first use; Accessibility is post-v1."""
    return {
        "microphone": {
            "granted": None,  # prompts on first voice use; no clean pre-check without pyobjc
            "label": "Microphone",
            "why": "Hear you so you can talk to Vee.",
            "note": "Allow when prompted on your first voice command.",
        },
        "full_disk_access": {
            "granted": _check_full_disk_access(),
            "label": "Full Disk Access",
            "why": "Read and act on your files anywhere.",
            "settings_pane": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        },
        "calendars": {
            "granted": _calendar_access_granted(),  # silent EventKit check, no prompt
            "label": "Calendar",
            "why": "Read and create events in your Calendar.",
            "settings_pane": "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
        },
        "automation": {
            "granted": None,  # macOS prompts the first time each app is targeted
            "label": "Automation",
            "why": "Drive Calendar, Mail, Notes and Chrome via AppleScript.",
            "note": "Granted per app the first time VALET controls it.",
        },
        "contacts": {
            "granted": _contacts_access_granted(),  # silent check, no prompt
            "label": "Contacts",
            "why": "Look up a name → email when you say 'email Nick'. Optional — your saved contacts work without it.",
            "settings_pane": "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts",
        },
        "accessibility": {
            "granted": _accessibility_access_granted(),  # real AXIsProcessTrusted check
            "label": "Accessibility",
            "why": "Let Vee read the screen and click or type in any app, not just scriptable ones.",
            "settings_pane": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        },
        "screen_recording": {
            "granted": _screen_recording_granted(),  # real CGPreflightScreenCaptureAccess check
            "label": "Screen Recording",
            "why": "See what's on your screen — capture the focused window so Vee can read it.",
            "settings_pane": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        },
    }


_SETTINGS_PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "full_disk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "calendars": "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
    "contacts": "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts",
}


@app.post("/api/permissions/open")
async def api_permissions_open(request: Request):
    """Open a specific System Settings privacy pane (onboarding deep-link)."""
    body = await request.json()
    url = _SETTINGS_PANES.get((body or {}).get("target", ""))
    if not url:
        return {"ok": False, "error": "unknown target"}
    try:
        subprocess.Popen(["open", url])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


@app.post("/api/permissions/trigger")
async def api_permissions_trigger(request: Request):
    """Fire the NATIVE macOS permission prompt for a target inline — no Settings
    hunt. Automation: send a harmless Apple Event to System Events; the first one
    makes macOS show the "VALET wants to control System Events" prompt. The
    AppleScript succeeds once granted and errors (-1743) if denied, so the
    return value doubles as the live grant status. Calendars: request full
    EventKit access (native prompt). Microphone uses getUserMedia in the webview;
    Full Disk Access has no inline prompt on macOS."""
    body = await request.json()
    target = (body or {}).get("target", "")
    if target == "calendars":
        try:
            import apple_calendar
            granted = await asyncio.to_thread(apple_calendar.request_access)
            return {"ok": True, "granted": bool(granted)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
    if target == "contacts":
        try:
            granted = await asyncio.to_thread(contacts_access.request_access)
            return {"ok": True, "granted": bool(granted)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
    if target == "accessibility":
        # Fire the native "grant Accessibility" prompt. The actual grant happens
        # in System Settings and usually needs an app relaunch to take effect, so
        # `granted` is the CURRENT trust state (typically still False right after
        # the prompt). The UI falls back to Open Settings and re-checks live.
        try:
            import accessibility_executor
            granted = await asyncio.to_thread(accessibility_executor.is_trusted_prompt)
            return {"ok": True, "granted": bool(granted)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
    if target == "screen_recording":
        # Fire the native Screen Recording prompt (CGRequestScreenCaptureAccess).
        # Same as Accessibility: the grant lands in System Settings and needs a
        # relaunch, so `granted` is the current state (usually still False here).
        try:
            import perception
            granted = await asyncio.to_thread(perception.request_screen_recording)
            return {"ok": True, "granted": bool(granted)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}
    if target != "automation":
        return {"ok": False, "error": "no inline prompt for this target"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", 'tell application "System Events" to count processes',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": True, "granted": False}
        return {"ok": True, "granted": proc.returncode == 0}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}

# ---------------------------------------------------------------------------
# Universal control (UC1) — Accessibility primitives
#
# These route through the same safety-gated `executor` as every other action:
# observe is Tier 0 (runs straight through); click / type / key_combo are Tier 1
# and raise a confirm card over the WebSocket (and honor the kill switch) before
# anything is synthesized. They are the integration seam the UC4 observe→act loop
# will drive; exposed here so the primitives are independently exercisable in a
# signed build.
# ---------------------------------------------------------------------------

@app.post("/api/ax/observe")
async def api_ax_observe(request: Request):
    """Enumerate the focused window's accessibility tree (Tier 0)."""
    body = await request.json() if await request.body() else {}
    res = await executor.observe_ui(app=(body or {}).get("app") or None)
    return res.to_dict()


@app.post("/api/perception/observe")
async def api_perception_observe(request: Request):
    """UC2 observation: focused-window screenshot + AX snapshot (Tier 0). Returns
    image metadata only — the base64 bytes stay out of the response/logs (they're
    an observation for the model, never an analytics payload)."""
    body = await request.json() if await request.body() else {}
    import perception
    obs = await perception.build_observation(executor, app=(body or {}).get("app") or None)
    img = obs.get("image")
    return {
        "app": obs["app"],
        "ax_ok": obs["ax_ok"],
        "screen_recording": obs["screen_recording"],
        "element_count": len(obs["elements"]),
        "elements": obs["elements"],
        "window_frame": obs["window_frame"],
        "image": None if not img else {
            "media_type": img["media_type"], "width": img["width"],
            "height": img["height"], "bytes": len(img["b64"]) * 3 // 4,
        },
    }


async def _resolve_and_act(action: str, target: str, text: str = "",
                           app: Optional[str] = None, *, task_id: Optional[str] = None) -> dict:
    """UC3: resolve a natural-language target against a fresh observation, then
    execute the click/type through the safety-gated executor (confirm card +
    kill switch). Ambiguous → ask; miss → honest fail; never a wild click.

    For 'type', the field is focused via benign AX (no synthetic input) so only
    the keystroke itself raises a confirm — a vision-point target has no ref, so
    it falls back to a (gated) point-click to focus."""
    import perception
    import target_resolver
    if not target:
        return {"ok": False, "status": "miss", "message": "Tell me what to act on, sir."}

    obs = await perception.build_observation(executor, app=app)
    intent = "type into" if action == "type" else "click"
    res = await target_resolver.resolve(obs, target, anthropic_client, intent=intent)

    if res.status == "ambiguous":
        return {"ok": False, "status": "ambiguous", "message": res.message,
                "alternatives": res.alternatives}
    if res.status == "miss":
        return {"ok": False, "status": "miss",
                "message": res.message or f"I don't see a '{target}', sir."}

    # Surface the chosen target on the process panel BEFORE acting (supervision).
    if task_id:
        await emit_step(task_id, f"Target: {res.label or target}",
                        detail=f"resolved via {res.via}", status="active")

    if action == "type":
        if res.status == "ref":
            await _ax_executor.focus_element(res.ref)          # benign focus, no confirm
        else:
            await executor.click_element(point=res.point, app=app)  # gated focus-click
        r = await executor.send_keystroke(app or "", text, task_id=task_id)
    else:
        if res.status == "ref":
            r = await executor.click_element(ref=res.ref, app=app, task_id=task_id)
        else:
            r = await executor.click_element(point=res.point, app=app, task_id=task_id)

    return {"ok": r.ok, "status": res.status, "via": res.via,
            "label": res.label, "message": r.message, "target": res.to_dict()}


async def _handle_ui_act(action: dict, ws) -> None:
    """Voice path for UC3. Runs on a background task (NOT the WS receive loop) so
    the confirm reply for the gated click/type can be read — same deadlock-safe
    pattern as `_confirm_and_dispatch`. Streams the chosen target to the process
    panel and speaks the outcome (asks on ambiguous, honest on miss)."""
    ui_action = action.get("ui_action", "click")
    target = action.get("target", "")
    text = action.get("text", "")
    label = f"{'Type into' if ui_action == 'type' else 'Click'} {target}"
    try:
        async with process_bus.task_context(label) as task_id:
            result = await _resolve_and_act(ui_action, target, text, None, task_id=task_id)
    except Exception as e:
        log.error(f"ui_act error: {e}")
        await _speak(ws, "I couldn't do that, sir.")
        return
    msg = result.get("message") or ("Done, sir." if result.get("ok") else "I couldn't do that, sir.")
    await _speak(ws, msg)


@app.post("/api/ui/act")
async def api_ui_act(request: Request):
    """UC3 — act on a natural-language target. Body: {action: click|type, target,
    text?, app?}. Routes through resolve → confirm card → gated execute."""
    body = (await request.json() if await request.body() else {}) or {}
    return await _resolve_and_act(
        (body.get("action") or "click").lower(),
        body.get("target") or "",
        body.get("text") or "",
        body.get("app") or None,
    )


@app.post("/api/ax/click")
async def api_ax_click(request: Request):
    """Click an element by `ref` (from a prior observe) or `point` [x, y] (Tier 1)."""
    body = (await request.json() if await request.body() else {}) or {}
    pt = body.get("point")
    res = await executor.click_element(
        ref=body.get("ref"),
        point=tuple(pt) if isinstance(pt, (list, tuple)) else None,
        app=body.get("app"),
    )
    return res.to_dict()


@app.post("/api/ax/type")
async def api_ax_type(request: Request):
    """Type text into the active (or named) app via synthetic input (Tier 1)."""
    body = (await request.json() if await request.body() else {}) or {}
    res = await executor.send_keystroke(
        body.get("app", "") or "", body.get("text", "") or "",
        press_enter=bool(body.get("press_enter")),
    )
    return res.to_dict()


@app.post("/api/ax/key")
async def api_ax_key(request: Request):
    """Send a modifier chord like 'cmd+s' (Tier 1)."""
    body = (await request.json() if await request.body() else {}) or {}
    res = await executor.key_combo(body.get("combo", "") or "", app=body.get("app"))
    return res.to_dict()


# ---------------------------------------------------------------------------
# Control endpoints (restart, fix-self)
# ---------------------------------------------------------------------------

@app.post("/api/restart")
async def api_restart():
    """Restart the VALET server (works in dev + packaged builds)."""
    log.info("Restart requested — shutting down in ~1 second")
    async def _restart():
        await asyncio.sleep(1.0)
        if os.environ.get("VALET_SHIPPED"):
            import restart
            restart.restart_self()  # exits; the Tauri shell respawns the sidecar
        else:
            cmd = [sys.executable, __file__, "--port", "8340", "--host", "0.0.0.0"]
            os.execv(sys.executable, cmd)
    asyncio.create_task(_restart())
    return {"status": "restarting"}


@app.post("/api/fix-self")
async def api_fix_self():
    """Enter work mode in the VALET repo — VALET can now fix himself."""
    valet_dir = str(Path(__file__).parent)
    # The work_session is per-WebSocket, so we set a flag that the handler picks up
    # For now, also open Terminal so user can see
    script = (
        'tell application "Terminal"\n'
        '    activate\n'
        f'    do script "cd {valet_dir} && claude --dangerously-skip-permissions"\n'
        'end tell'
    )
    await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    log.info("Work mode: VALET repo opened for self-improvement")
    return {"status": "work_mode_active", "path": valet_dir}


# ---------------------------------------------------------------------------
# Static file serving (frontend)
# ---------------------------------------------------------------------------

from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    @app.get("/")
    async def serve_index():
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="VALET Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8340, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    parser.add_argument("--ssl", action="store_true", help="Enable HTTPS with key.pem/cert.pem")
    args = parser.parse_args()

    # Auto-detect SSL certs
    cert_file = Path(__file__).parent / "cert.pem"
    key_file = Path(__file__).parent / "key.pem"
    use_ssl = args.ssl or (cert_file.exists() and key_file.exists())

    proto = "https" if use_ssl else "http"
    ws_proto = "wss" if use_ssl else "ws"

    print()
    print("  VALET Server v0.1.0")
    print(f"  WebSocket: {ws_proto}://{args.host}:{args.port}/ws/voice")
    print(f"  REST API:  {proto}://{args.host}:{args.port}/api/")
    print(f"  Tasks:     {proto}://{args.host}:{args.port}/api/tasks")
    print()

    ssl_kwargs = {}
    if use_ssl:
        ssl_kwargs["ssl_keyfile"] = str(key_file)
        ssl_kwargs["ssl_certfile"] = str(cert_file)

    if getattr(sys, "frozen", False):
        # In a PyInstaller bundle there is no importable "server" module — pass
        # the app object directly (reload/workers don't apply to a frozen app).
        # Wait out any previous backend still releasing the port (permission-toggle
        # restarts), so binding never fails on a fast relaunch.
        _await_port_free(args.host, args.port)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", **ssl_kwargs)
    else:
        uvicorn.run(
            "server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
            **ssl_kwargs,
        )
