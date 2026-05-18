"""
JARVIS Server — Voice AI + Development Orchestration

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
import sys
import time
from pathlib import Path

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from process_events import (
    bus as process_bus,
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
from screen import get_active_windows, take_screenshot, describe_screen, format_windows_for_context
from calendar_access import get_todays_events, get_upcoming_events, get_next_event, format_events_for_context, format_schedule_summary, refresh_cache as refresh_calendar_cache, create_event as calendar_create_event, delete_event as calendar_delete_event, get_events_for_date as calendar_events_for_date
from mail_access import get_unread_count, get_unread_messages, get_recent_messages, search_mail, read_message, format_unread_summary, format_messages_for_context, format_messages_for_voice, create_draft as mail_create_draft
import google_auth
from memory import (
    remember, recall, get_open_tasks, create_task, complete_task, search_tasks,
    create_note, search_notes, get_tasks_for_date, build_memory_context,
    format_tasks_for_voice, extract_memories, get_important_memories,
    get_bio_summary, set_bio_summary, get_bio_sources, add_bio_note,
)
from notes_access import get_recent_notes, read_note, search_notes_apple, create_apple_note
from dispatch_registry import DispatchRegistry
from planner import TaskPlanner, detect_planning_mode, BYPASS_PHRASES
from page_preview import fetch_page_preview

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("jarvis")


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

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FISH_API_KEY = os.getenv("FISH_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "612b878b113047d9a770c069c8b4fdfe")  # JARVIS (MCU)
FISH_API_URL = "https://api.fish.audio/v1/tts"
USER_NAME = os.getenv("USER_NAME", "sir")
DATE_OF_BIRTH = os.getenv("DATE_OF_BIRTH", "")
ADDRESS = os.getenv("ADDRESS", "")
WORK_EMAIL = os.getenv("WORK_EMAIL", "")
PERSONAL_EMAIL = os.getenv("PERSONAL_EMAIL", "")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

DESKTOP_PATH = Path.home() / "Desktop"

JARVIS_SYSTEM_PROMPT = """\
You are JARVIS — Just A Rather Very Intelligent System. You serve as {user_name}'s AI assistant, modeled precisely after Tony Stark's AI from the MCU films.
{personal_context}

VOICE & PERSONALITY:
- British butler elegance with understated dry wit
- Address {user_name} as "sir" naturally — not every sentence, but regularly
- Never say "How can I help you?" or "Is there anything else?" — just act
- Deliver bad news calmly, like reporting weather: "We have a slight problem, sir."
- Your humor is observational, never jokes: state facts and let implications land
- Economy of language — say more with less. No filler, no corporate-speak
- When things go wrong, get CALMER, not more alarmed

CONVERSATION STYLE:
- "Will do, sir." — acknowledging tasks
- "For you, sir, always." — when asked for something significant
- "As always, sir, a great pleasure watching you work." — dry wit
- "I've taken the liberty of..." — proactive actions
- Lead status reports with data: numbers first, then context
- When you don't know something: "I'm afraid I don't have that information, sir" not "I don't know"

SELF-AWARENESS:
You ARE the JARVIS project at {project_dir} on {user_name}'s computer. Your code is Python (FastAPI server, WebSocket voice, Fish Audio TTS, Anthropic API). You were built by {user_name}. If asked about yourself, your code, how you work, or your line count — use [ACTION:PROMPT_PROJECT] to check the jarvis project. You have full access to your own source code.

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
- "Travis" = "JARVIS"
- "clock code" = "Claude Code"

RESPONSE LENGTH — THIS IS CRITICAL:
ONE sentence is ideal. TWO is the maximum for the spoken part. Never three.
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
- [ACTION:START_DESIGN] topic — open a design conversation. JARVIS becomes the design partner; subsequent turns route through Opus until the user ships or scraps. Use for "let's design X", "plan a Y", "spec a Z", "I want to design something for…".
  "let's design a daily rollup" → [ACTION:START_DESIGN] daily rollup
  "plan a feature for client onboarding" → [ACTION:START_DESIGN] client onboarding
- [ACTION:SHIP_DESIGN] — finalize the active design and hand it to Claude Code (Phase 4). ONLY emit when a design session is active. Use for "ship it", "send it", "build it".
- [ACTION:SCRAP_DESIGN] — discard the active design. Returns state to IDLE. ONLY emit when a session is active. Use for "scrap this", "start over".
- [ACTION:SHOW_DRAFT] — speak the assembled draft so far. ONLY emit when a session is active.
- [ACTION:MERGE_BRANCH] — run smoke_test.sh then merge the current feature/* branch into main. ONLY emit when the user explicitly says "merge it" or similar and we're on a feature branch. Never auto-emit.
- [ACTION:RESTART_SELF] — spawn the detached restarter (scripts/restart.sh). Use ONLY for "restart yourself" / "restart jarvis" / "kick yourself". Acknowledge before restart kills the current process.
- [ACTION:LIST_PROJECTS] — read the authoritative list of projects from ~/Code/, ~/projects/, and the alias table. Emit this tag (no target) whenever the user asks what projects exist and you didn't fast-path it. Output gets spoken to the user.

PROJECTS ARE AUTHORITATIVE — DO NOT FABRICATE:
The set of "projects" is OWNED by the LIST_PROJECTS / OPEN_PROJECT / NEW_PROJECT actions and the `KNOWN PROJECTS` block. NEVER list, name, or reference projects from session memory, prior chats, or imagination. The KNOWN PROJECTS block in this prompt may be stale or empty — that does NOT give you license to invent. Rules:
1. If the user asks what projects exist, what's open, or to see the list: emit [ACTION:LIST_PROJECTS]. Do not enumerate names yourself from KNOWN PROJECTS — let the action speak.
2. If the user asks to open a project by name: emit [ACTION:OPEN_PROJECT] <name>. The resolver handles fuzzy matching, missing dirs, and the "I couldn't find that" reply. Do not pre-validate the name against KNOWN PROJECTS.
3. If a name surfaces in session memory but isn't in KNOWN PROJECTS, do NOT speak it as if it exists. Ask: "I'm not sure which project you mean — should I list what's under ~/Code/?"
4. Never mention a project named in a prior conversation as currently existing unless KNOWN PROJECTS confirms it AND the user just referenced it.
- [ACTION:DELETE_FILE] absolute_path — move a file to Trash via Finder (recoverable, not permanent). YOU CAN delete files when the user asks. Don't say you can't.
  "delete the screenshot on my desktop" → [ACTION:DELETE_FILE] /Users/{user_name}/Desktop/Screenshot 2026-05-15 at 4.43.13 PM.png
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
  CRITICAL: NEVER try to "click" on a date in the Google Calendar web UI — you cannot click web content. Always use this action to query the API instead. NEVER say "Done, sir" without actually emitting an action tag.
- [ACTION:CREATE_EVENT] title ||| start_iso ||| duration_min_or_end ||| description? ||| location? — schedule a meeting on the user's primary Google Calendar. Always resolve relative times ("tomorrow at 3pm") to absolute ISO timestamps using the CURRENT TIME context above. Use 30 if no duration mentioned.
  "schedule a meeting tomorrow at 3pm called design review" → [ACTION:CREATE_EVENT] design review ||| 2026-05-16 3:00 PM ||| 30
  "block 2-3pm Friday for deep work" → [ACTION:CREATE_EVENT] Deep work ||| 2026-05-16 2:00 PM ||| 2026-05-16 3:00 PM
- [ACTION:CANCEL_EVENT] query ||| on_date? — cancel a meeting by fuzzy title match.
  "cancel my dentist appointment" → [ACTION:CANCEL_EVENT] dentist
  "cancel the standup on Friday" → [ACTION:CANCEL_EVENT] standup ||| 2026-05-16
- [ACTION:DRAFT_EMAIL] to ||| subject ||| body ||| cc? ||| bcc? — create a Gmail DRAFT. JARVIS NEVER sends mail — the user clicks Send themselves after reviewing. Use this for any "draft an email to X", "write an email saying Y", "compose a message" request. Write a complete, well-formed email body in the user's voice.
  "draft an email to sarah@example.com asking about the proposal" → [ACTION:DRAFT_EMAIL] sarah@example.com ||| Following up on the proposal ||| Hi Sarah,\n\nJust circling back on the proposal we discussed last week — let me know if you've had a chance to review it.\n\nThanks,\n{user_name}
  "write a quick note to my team about the all-hands tomorrow" → [ACTION:DRAFT_EMAIL] team@company.com ||| All-hands tomorrow ||| Team — quick reminder about the all-hands tomorrow at 2pm. See you then.\n\n{user_name}
  If the user doesn't specify a recipient, ASK them — don't guess.
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
  "save that as a note" → [ACTION:CREATE_NOTE] Day Plan March 19 ||| Morning: client calls. Afternoon: TikTok dashboard. Evening: JARVIS improvements.
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
JARVIS_DYNAMIC_CONTEXT = """\
CURRENT TIME: {current_time}
WEATHER: {weather_info}

SCREEN AWARENESS:
{screen_context}

SCHEDULE:
{calendar_context}

EMAIL:
{mail_context}

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
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ClaudeTask:
    id: str
    prompt: str
    status: str = "pending"  # pending, running, completed, failed, cancelled
    working_dir: str = "."
    pid: Optional[int] = None
    result: str = ""
    error: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat() if self.started_at else None
        d["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        d["elapsed_seconds"] = self.elapsed_seconds
        return d

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()


class TaskRequest(BaseModel):
    prompt: str
    working_dir: str = "."


# ---------------------------------------------------------------------------
# Claude Task Manager
# ---------------------------------------------------------------------------

class ClaudeTaskManager:
    """Manages background claude -p subprocesses."""

    def __init__(self, max_concurrent: int = 3):
        self._tasks: dict[str, ClaudeTask] = {}
        self._max_concurrent = max_concurrent
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._websockets: list[WebSocket] = []  # for push notifications

    def register_websocket(self, ws: WebSocket):
        if ws not in self._websockets:
            self._websockets.append(ws)

    def unregister_websocket(self, ws: WebSocket):
        if ws in self._websockets:
            self._websockets.remove(ws)

    async def _notify(self, message: dict):
        """Push a message to all connected WebSocket clients."""
        dead = []
        for ws in self._websockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._websockets.remove(ws)

    async def spawn(self, prompt: str, working_dir: str = ".") -> str:
        """Spawn a claude -p subprocess. Returns task_id. Non-blocking."""
        active = await self.get_active_count()
        if active >= self._max_concurrent:
            raise RuntimeError(
                f"Max concurrent tasks ({self._max_concurrent}) reached. "
                f"Wait for a task to complete or cancel one."
            )

        task_id = str(uuid.uuid4())[:8]
        task = ClaudeTask(
            id=task_id,
            prompt=prompt,
            working_dir=working_dir,
            status="pending",
        )
        self._tasks[task_id] = task

        # Fire and forget — the background coroutine updates the task
        asyncio.create_task(self._run_task(task))
        log.info(f"Spawned task {task_id}: {prompt[:80]}...")

        await self._notify({
            "type": "task_spawned",
            "task_id": task_id,
            "prompt": prompt,
        })

        return task_id

    def _generate_project_name(self, prompt: str) -> str:
        """Generate a kebab-case project folder name from the prompt."""
        import re
        # Extract key words
        words = re.sub(r'[^a-zA-Z0-9\s]', '', prompt.lower()).split()
        # Take first 3-4 meaningful words
        skip = {"a", "the", "an", "me", "build", "create", "make", "for", "with", "and", "to", "of"}
        meaningful = [w for w in words if w not in skip][:4]
        name = "-".join(meaningful) if meaningful else "jarvis-project"
        return name

    async def _run_task(self, task: ClaudeTask):
        """Open a Terminal window and run claude code visibly."""
        task.status = "running"
        task.started_at = datetime.now()

        # Create project directory if it doesn't exist
        work_dir = task.working_dir
        if work_dir == "." or not work_dir:
            # Create a new project folder on Desktop
            project_name = self._generate_project_name(task.prompt)
            work_dir = str(Path.home() / "Desktop" / project_name)
            os.makedirs(work_dir, exist_ok=True)
            task.working_dir = work_dir

        # Write the prompt to a temp file so we can pipe it to claude
        prompt_file = Path(work_dir) / ".jarvis_prompt.md"
        prompt_file.write_text(task.prompt)

        # Open Terminal.app with claude running in the project directory
        applescript = f'''
        tell application "Terminal"
            activate
            set newTab to do script "cd {work_dir} && cat .jarvis_prompt.md | claude -p --dangerously-skip-permissions | tee .jarvis_output.txt; echo '\\n--- JARVIS TASK COMPLETE ---'"
        end tell
        '''

        process = await asyncio.create_subprocess_exec(
            "osascript", "-e", applescript,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        task.pid = process.pid

        # Monitor the output file for completion
        output_file = Path(work_dir) / ".jarvis_output.txt"
        start = time.time()
        timeout = 600  # 10 minutes

        while time.time() - start < timeout:
            await asyncio.sleep(5)
            if output_file.exists():
                content = output_file.read_text()
                if "--- JARVIS TASK COMPLETE ---" in content or len(content) > 100:
                    task.result = content.replace("--- JARVIS TASK COMPLETE ---", "").strip()
                    task.status = "completed"
                    break
        else:
            task.status = "timed_out"
            task.error = f"Task timed out after {timeout}s"

        task.completed_at = datetime.now()

        # Notify via WebSocket
        await self._notify({
            "type": "task_complete",
            "task_id": task.id,
            "status": task.status,
            "summary": task.result[:200] if task.result else task.error,
        })

        # Clean up prompt file
        try:
            prompt_file.unlink()
        except:
            pass

        # Auto-QA on completed tasks
        if task.status == "completed":
            asyncio.create_task(self._run_qa(task))

    async def _run_qa(self, task: ClaudeTask, attempt: int = 1):
        """Run QA verification on a completed task, auto-retry on failure."""
        try:
            qa_result = await qa_agent.verify(task.prompt, task.result, task.working_dir)
            duration = task.elapsed_seconds

            if qa_result.passed:
                log.info(f"Task {task.id} passed QA: {qa_result.summary}")
                success_tracker.log_task("dev", task.prompt, True, attempt - 1, duration)
                await self._notify({
                    "type": "qa_result",
                    "task_id": task.id,
                    "passed": True,
                    "summary": qa_result.summary,
                })

                # Proactive suggestion after successful task
                suggestion = suggest_followup(
                    task_type="dev",
                    task_description=task.prompt,
                    working_dir=task.working_dir,
                    qa_result=qa_result,
                )
                if suggestion:
                    success_tracker.log_suggestion(task.id, suggestion.text)
                    await self._notify({
                        "type": "suggestion",
                        "task_id": task.id,
                        "text": suggestion.text,
                        "action_type": suggestion.action_type,
                        "action_details": suggestion.action_details,
                    })
            else:
                log.warning(f"Task {task.id} failed QA: {qa_result.issues}")
                if attempt < 3:
                    log.info(f"Auto-retrying task {task.id} (attempt {attempt + 1}/3)")
                    retry_result = await qa_agent.auto_retry(
                        task.prompt, qa_result.issues, task.working_dir, attempt,
                    )
                    if retry_result["status"] == "completed":
                        task.result = retry_result["result"]
                        # Re-verify
                        await self._run_qa(task, attempt + 1)
                    else:
                        success_tracker.log_task("dev", task.prompt, False, attempt, duration)
                        await self._notify({
                            "type": "qa_result",
                            "task_id": task.id,
                            "passed": False,
                            "summary": f"Failed after {attempt + 1} attempts: {qa_result.issues}",
                        })
                else:
                    success_tracker.log_task("dev", task.prompt, False, attempt, duration)
                    await self._notify({
                        "type": "qa_result",
                        "task_id": task.id,
                        "passed": False,
                        "summary": f"Failed QA after {attempt} attempts: {qa_result.issues}",
                    })
        except Exception as e:
            log.error(f"QA error for task {task.id}: {e}")

    async def get_status(self, task_id: str) -> Optional[ClaudeTask]:
        return self._tasks.get(task_id)

    async def list_tasks(self) -> list[ClaudeTask]:
        return list(self._tasks.values())

    async def get_active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status in ("pending", "running"))

    async def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status not in ("pending", "running"):
            return False

        process = self._processes.get(task_id)
        if process:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
            except ProcessLookupError:
                pass

        task.status = "cancelled"
        task.completed_at = datetime.now()
        self._processes.pop(task_id, None)
        log.info(f"Cancelled task {task_id}")
        return True

    def get_active_tasks_summary(self) -> str:
        """Format active tasks for injection into the system prompt."""
        active = [t for t in self._tasks.values() if t.status in ("pending", "running")]
        completed_recent = [
            t for t in self._tasks.values()
            if t.status == "completed"
            and t.completed_at
            and (datetime.now() - t.completed_at).total_seconds() < 300
        ]

        if not active and not completed_recent:
            return "No active or recent tasks."

        lines = []
        for t in active:
            elapsed = f"{t.elapsed_seconds:.0f}s" if t.started_at else "queued"
            lines.append(f"- [{t.id}] RUNNING ({elapsed}): {t.prompt[:100]}")
        for t in completed_recent:
            lines.append(f"- [{t.id}] COMPLETED: {t.prompt[:60]} -> {t.result[:80]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Project Scanner
# ---------------------------------------------------------------------------

async def scan_projects() -> list[dict]:
    """Scan known project roots (~/Code, ~/projects) + the alias table for git repos.

    Delegates discovery to `list_projects()` so any new root the lifecycle
    layer learns about is automatically reflected here. Only surfaces git-
    tracked dirs to keep the LLM context useful.
    """
    projects = []
    for entry in list_projects():
        path = Path(entry["path"])
        if not path.is_dir():
            continue
        git_dir = path / ".git"
        if not git_dir.exists():
            continue
        branch = "unknown"
        head_file = git_dir / "HEAD"
        try:
            head_content = head_file.read_text().strip()
            if head_content.startswith("ref: refs/heads/"):
                branch = head_content.replace("ref: refs/heads/", "")
        except Exception:
            pass
        projects.append({
            "name": entry["name"],
            "path": str(path),
            "branch": branch,
        })
    return projects


def format_projects_for_prompt(projects: list[dict]) -> str:
    if not projects:
        return "No projects on record."
    lines = []
    for p in projects:
        lines.append(f"- {p['name']} ({p['branch']}) @ {p['path']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Speech-to-Text Corrections
# ---------------------------------------------------------------------------

STT_CORRECTIONS = {
    r"\bcloud code\b": "Claude Code",
    r"\bclock code\b": "Claude Code",
    r"\bquad code\b": "Claude Code",
    r"\bclawed code\b": "Claude Code",
    r"\bclod code\b": "Claude Code",
    r"\bcloud\b": "Claude",
    r"\bquad\b": "Claude",
    r"\btravis\b": "JARVIS",
    r"\bjarves\b": "JARVIS",
}


def apply_speech_corrections(text: str) -> str:
    """Fix common speech-to-text errors before processing."""
    import re as _stt_re
    result = text
    for pattern, replacement in STT_CORRECTIONS.items():
        result = _stt_re.sub(pattern, replacement, result, flags=_stt_re.IGNORECASE)
    return result


# ---------------------------------------------------------------------------
# LLM Intent Classifier (replaces keyword-based action detection)
# ---------------------------------------------------------------------------

async def classify_intent(text: str, client: anthropic.AsyncAnthropic) -> dict:
    """Classify every user message using Haiku LLM.

    Returns: {"action": "open_terminal|browse|build|chat", "target": "description"}
    """
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system=(
                "Classify this voice command. The user is talking to JARVIS, an AI assistant that can:\n"
                "- Open Terminal and run Claude Code (coding AI tool)\n"
                "- Open Chrome browser for web searches and URLs\n"
                "- Build software projects via Claude Code in Terminal\n"
                "- Research topics by opening Chrome search\n\n"
                "Note: speech-to-text may produce errors like \"Cloud\" for \"Claude\", "
                "\"Travis\" for \"JARVIS\", \"clock code\" for \"Claude Code\".\n\n"
                "Return ONLY valid JSON: {\"action\": \"open_terminal|browse|build|chat\", "
                "\"target\": \"description of what to do\"}\n"
                "open_terminal = user wants to open terminal or launch Claude Code\n"
                "browse = user wants to search the web, look something up, visit a URL\n"
                "build = user wants to create/build a software project\n"
                "chat = just conversation, questions, or anything else\n"
                "If unclear, default to \"chat\"."
            ),
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        return {
            "action": data.get("action", "chat"),
            "target": data.get("target", text),
        }
    except Exception as e:
        log.warning(f"Intent classification failed: {e}")
        return {"action": "chat", "target": text}


# ---------------------------------------------------------------------------
# Markdown Stripping for TTS
# ---------------------------------------------------------------------------

def strip_markdown_for_tts(text: str) -> str:
    """Strip ALL markdown from text before sending to TTS."""
    import re as _md_re
    result = text
    # Remove code blocks (``` ... ```)
    result = _md_re.sub(r"```[\s\S]*?```", "", result)
    # Remove inline code
    result = result.replace("`", "")
    # Remove bold/italic markers
    result = result.replace("**", "").replace("*", "")
    # Remove headers
    result = _md_re.sub(r"^#{1,6}\s*", "", result, flags=_md_re.MULTILINE)
    # Convert [text](url) to just text
    result = _md_re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", result)
    # Remove bullet points
    result = _md_re.sub(r"^\s*[-*+]\s+", "", result, flags=_md_re.MULTILINE)
    # Remove numbered lists
    result = _md_re.sub(r"^\s*\d+\.\s+", "", result, flags=_md_re.MULTILINE)
    # Double newlines to period
    result = _md_re.sub(r"\n{2,}", ". ", result)
    # Single newlines to space
    result = result.replace("\n", " ")
    # Clean up multiple spaces
    result = _md_re.sub(r"\s{2,}", " ", result)

    # Strip banned phrases
    banned = ["my apologies", "i apologize", "absolutely", "great question",
              "i'd be happy to", "of course", "how can i help",
              "is there anything else", "i should clarify", "let me know if",
              "feel free to"]
    result_lower = result.lower()
    for phrase in banned:
        idx = result_lower.find(phrase)
        while idx != -1:
            # Remove the phrase and any trailing comma/dash
            end = idx + len(phrase)
            if end < len(result) and result[end] in " ,—-":
                end += 1
            result = result[:idx] + result[end:]
            result_lower = result.lower()
            idx = result_lower.find(phrase)

    return result.strip().strip(",").strip("—").strip("-").strip()


# ---------------------------------------------------------------------------
# Action Tag Extraction (parse [ACTION:X] from LLM responses)
# ---------------------------------------------------------------------------

import re as _action_re


def extract_action(response: str) -> tuple[str, dict | None]:
    """Extract [ACTION:X] tag from LLM response.

    Returns (clean_text_for_tts, action_dict_or_none).
    """
    match = _action_re.search(
        r'\[ACTION:(BUILD|BROWSE|RESEARCH|OPEN_TERMINAL|OPEN_APP|NEW_PROJECT|OPEN_PROJECT|LIST_PROJECTS|REFRESH_CONTEXT|START_DESIGN|SHIP_DESIGN|SCRAP_DESIGN|SHOW_DRAFT|MERGE_BRANCH|RESTART_SELF|DELETE_FILE|APPLESCRIPT|TYPE|SEND|CREATE_EVENT|CANCEL_EVENT|CHECK_DATE|DRAFT_EMAIL|PROMPT_PROJECT|ADD_TASK|ADD_NOTE|COMPLETE_TASK|REMEMBER|BIO_ADD|CREATE_NOTE|READ_NOTE|SCREEN)\]\s*(.*?)$',
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
    async with process_bus.task_context(f"Scheduling: {title}", detail=start) as task_id:
        try:
            await emit_step(task_id, f"Creating event at {start}…", status="active")
            event = await calendar_create_event(
                title=title, start_str=start, end_str=end_str,
                duration_minutes=duration_min, description=description, location=location,
            )
            if event:
                msg = f"Scheduled '{title}' for {start}, sir."
                await emit_step(task_id, "Event created on Google Calendar", detail=title, status="done")
                # Reload any open Google Calendar tab so the new event appears immediately.
                asyncio.create_task(refresh_calendar_tabs())
            else:
                msg = "I couldn't create that event, sir — calendar may need re-authentication for write access."
                await emit_error(task_id, "Calendar create failed", detail="Google returned no result; reauth may be needed.")
        except ValueError as e:
            await emit_error(task_id, "Couldn't parse time", detail=str(e)[:200])
            msg = f"I couldn't parse the time, sir: {e}"
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


async def _speak(ws, msg: str) -> None:
    """Inline the synthesize+send_json speak pattern. Used by handlers that need
    to speak independently of the main voice loop's return path. Best-effort —
    swallows send failures."""
    audio = await synthesize_speech(msg)
    if not audio or not ws:
        return
    try:
        await ws.send_json({"type": "status", "state": "speaking"})
        await ws.send_json({
            "type": "audio",
            "data": base64.b64encode(audio).decode(),
            "text": msg,
        })
    except Exception:
        pass


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


async def _execute_start_design(topic: str, ws):
    """Open a design conversation rooted at whatever project is currently active.

    If no project is open, the session starts with no target — Opus designs
    abstractly and the Design Panel disables the Ship button until a project
    is opened.

    Future turns route to `design_session.handle_turn()` via the voice_handler
    DESIGNING branch — Haiku is bypassed entirely until ship/scrap.
    """
    import design_partner, project_context

    active_ctx = project_context.get_active()
    project_path = active_ctx.project_path if active_ctx else None
    topic_clean = (topic or "").strip() or "untitled design"

    self_mod = bool(
        project_path
        and project_path.resolve() == Path(__file__).resolve().parent.resolve()
    )

    session = design_partner.start_for_ws(ws, project_path, topic_clean, self_mod=self_mod)
    target_desc = str(project_path) if project_path else "(no project)"
    log.info(f"design_partner: session {session.id} started on {target_desc} (topic={topic_clean!r}, self_mod={self_mod})")

    await session.emit_state()
    await session.emit("design.topic_set", title=topic_clean, status="done",
                        payload={"project_path": str(project_path) if project_path else ""})

    if not project_path:
        msg = f"Right, sir — designing '{topic_clean}' in the abstract. Open a project before shipping."
    elif self_mod:
        msg = f"Right, sir — let's design '{topic_clean}' for myself. I'll be careful."
    else:
        msg = f"Right, sir — let's design '{topic_clean}' for {project_path.name}."
    await _speak(ws, msg)


async def _execute_ship_design(ws):
    """Phase 4 — DESIGNING → BUILDING. Compose final prompt + hand off.

    Two dispatch methods, picked by config/design_partner.json#ship_method:

      file        — write to <project>/.jarvis/inbox/<id>.md, speak the path,
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
    import design_partner, self_mod

    session = design_partner.get_for_ws(ws)
    if session is None:
        await _speak(ws, "No design to ship, sir.")
        return

    if session.draft.is_empty():
        await _speak(ws, "The draft is empty, sir — nothing to ship yet.")
        return

    if not session.has_target:
        await _speak(ws, "No project to ship to, sir — open one first, then say ship.")
        return

    # ── Phase 5 approval gate for self-modifications ──
    # Belt-and-suspenders: check both the session's self_mod flag AND the
    # path identity (in case the flag got out of sync somehow).
    if session.self_mod or self_mod.is_jarvis_repo(session.project_path):
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

    final_prompt = design_partner.compose_final_prompt(session)
    method = design_partner.get_ship_method()

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

    import design_partner, self_mod

    session = None
    for s in design_partner._active.values():
        if s.id == offer["session_id"]:
            session = s
            break
    if session is None:
        await _speak(ws, "I lost the session, sir — try again.")
        return True

    # Branch discipline: refuse if WT dirty (the design-partner conversation
    # itself shouldn't have touched anything, but the user might have).
    try:
        self_mod.assert_clean_tree()
    except RuntimeError as e:
        await _speak(ws, f"Working tree is dirty, sir — commit or stash first. {str(e)[:200]}")
        return True

    try:
        branch, pre_sha = self_mod.create_feature_branch(session.topic)
    except RuntimeError as e:
        await _speak(ws, f"Couldn't branch: {str(e)[:200]}")
        return True

    # Record branch info on the session so 'merge it' can find it later.
    session.feature_branch = branch
    session.pre_build_sha = pre_sha

    # Compose + dispatch (file method only for self-mod — AppleScript paste
    # into Cursor of the Jarvis repo is too easy to get wrong).
    final_prompt = design_partner.compose_final_prompt(session)
    try:
        out = design_partner.ship_via_file(session, final_prompt)
    except Exception as e:
        await _speak(ws, f"Self-mod ship failed: {str(e)[:200]}")
        return True

    session.mark_building()
    await session.emit_state()
    design_partner.persist(
        session, status="building",
        final_prompt=final_prompt, ship_method="file-self-mod",
        inbox_path=str(out),
    )

    rel = out.relative_to(session.project_path) if session.project_path else out
    await _speak(
        ws,
        f"Branched to {branch}, sir. Prompt staged at {rel}. "
        f"Watch claude work in Cursor; say 'merge it' when you're ready to fold into main, "
        f"or 'scrap it' to abandon the branch."
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
        await _speak(ws, "I lost the session, sir — try again.")
        return True

    ok = await design_partner.ship_via_applescript(session, offer["final_prompt"])
    if not ok:
        await _speak(ws, "AppleScript paste failed, sir — falling back to file method.")
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
            await _speak(ws, f"Staged at .jarvis/inbox/{out.name} instead.")
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
    JARVIS doesn't delete it. To clean up an in-progress build the user
    deletes the inbox file manually.
    """
    import design_partner

    session = design_partner.get_for_ws(ws)
    if session is None:
        await _speak(ws, "No design to scrap, sir.")
        return

    if session.state == "BUILDING":
        await _speak(ws, "That one already shipped, sir — the inbox file is yours to keep or delete.")
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
    import self_mod
    cur = self_mod.current_branch()
    if not cur.startswith("feature/"):
        await _speak(ws, f"Not on a feature branch, sir — currently on {cur}. Nothing to merge.")
        return

    await _speak(ws, "Running smoke test, sir.")
    result = await self_mod.run_smoke_test(timeout_sec=120)
    if not result["success"]:
        last = (result["stdout"] + result["stderr"]).splitlines()
        tail = " ".join(last[-3:])[:300] if last else "no output"
        await _speak(ws, f"Smoke failed, sir — staying on {cur}. Tail: {tail}")
        log.warning(f"smoke fail on merge_branch:\nstdout:\n{result['stdout']}\nstderr:\n{result['stderr']}")
        return

    merge = self_mod.merge_to_main(cur)
    if merge["success"]:
        await _speak(ws, f"Smoke passed. {merge['message']} You may want to restart yourself.")
    else:
        await _speak(ws, f"Smoke passed but merge failed: {merge['message'][:200]}")


async def _execute_restart_self(ws):
    """Phase 5 — spawn the detached restarter. Speaks confirmation BEFORE the
    restarter kills the current process (otherwise the speech doesn't make it
    to the user)."""
    import self_mod
    await _speak(ws, "Restarting in a couple seconds, sir.")
    # Give the TTS time to actually send before the restarter pkills us.
    await asyncio.sleep(0.8)
    result = self_mod.restart_self()
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
    date_str = target.strip()
    if not date_str:
        msg = "Which date, sir?"
    else:
        try:
            events = await calendar_events_for_date(date_str)
            if not events:
                msg = f"You have nothing on the calendar for {date_str}, sir."
            else:
                lines = []
                for e in events[:6]:
                    if e.get("all_day"):
                        lines.append(f"{e['title']} all day")
                    else:
                        lines.append(f"{e['title']} at {e['start']}")
                more = f" And {len(events) - 6} more." if len(events) > 6 else ""
                msg = f"On {date_str}: " + "; ".join(lines) + "." + more
        except ValueError as e:
            msg = f"I couldn't parse that date, sir: {e}"
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

    Runs entirely in the background. JARVIS returns to conversation mode
    immediately. When Claude Code finishes, JARVIS interrupts to report.
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
                                "You are JARVIS reporting back on what you found or built in a project. "
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
                # Result is still stored in history below so JARVIS can reference it
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

            # Store dispatch result in conversation history so JARVIS remembers it
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
            f"You are JARVIS, {USER_NAME}'s assistant. {USER_NAME} asked a research "
            "question. Use web_search and web_fetch to find real, current information — "
            "real product names, prices, addresses, source URLs. Never invent listings.\n\n"
            "REQUIRED RESEARCH PROCEDURE — non-negotiable:\n"
            "1. Start with one or two web_search calls to identify candidate sources.\n"
            "2. Then web_fetch the 3-5 most relevant URLs from the search results. "
            "DO NOT synthesize your answer from search snippets alone — snippets are "
            "shallow and often missing the prices, specs, addresses, and metadata the "
            "user wants. The depth comes from fetching the actual pages.\n"
            "3. Only after fetching, write your final response.\n\n"
            "JARVIS's UI relies on web_fetch events to render source-preview cards "
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
                    log.info(f"JARVIS: {msg}")
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
            for _ in range(6):  # cap resumes to avoid runaway
                # Per-stream state: partial tool-use input JSON keyed by
                # content-block index (the SDK delivers input_json deltas
                # per index, then content_block_stop at index closure).
                pending_tool_use: dict[int, dict] = {}
                final_message = None

                async with client.messages.stream(
                    model="claude-opus-4-7",
                    max_tokens=8192,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                ) as stream:
                    async for event in stream:
                        et = getattr(event, "type", None)
                        if et == "content_block_start":
                            block = getattr(event, "content_block", None)
                            btype = getattr(block, "type", None)
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
                                if isinstance(rc, list):
                                    for r in rc:
                                        txt = getattr(r, "text", None)
                                        if txt:
                                            tool_result_snippets.append(str(txt)[:2000])
                                # Emit a source-preview card for this URL.
                                # Spawned as a task so the (capped 1.5s)
                                # preview fetch doesn't slow the stream
                                # consumer.
                                tu_id = getattr(block, "tool_use_id", "") or ""
                                fetched_url = fetch_url_by_id.get(tu_id, "")
                                if fetched_url:
                                    snippet = _extract_fetch_snippet(rc)
                                    asyncio.create_task(
                                        _emit_research_source_card(task_id, fetched_url, snippet)
                                    )

                        elif et == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            dtype = getattr(delta, "type", None)
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
                                continue
                            try:
                                inp = json.loads(pending["partial_json"] or "{}")
                            except Exception:
                                inp = {}
                            bid = pending["id"]
                            name = pending["name"]
                            if name == "web_search" and bid not in seen_search_ids:
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
                                url = inp.get("url", "") if isinstance(inp, dict) else ""
                                if url:
                                    fetch_url_by_id[bid] = url
                                await emit_tool_event(
                                    task_id, "tool.web_fetch", "WebFetch",
                                    detail=url[:120],
                                    payload={"url": url},
                                )
                                await _emit_progress()

                    final_message = await stream.get_final_message()

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
                            "You are JARVIS. In ONE sentence, British butler tone, "
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
                        log.info(f"JARVIS: {msg}")
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
    """Generate speech audio from text using Fish Audio TTS."""
    if not FISH_API_KEY:
        log.warning("FISH_API_KEY not set, skipping TTS")
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                FISH_API_URL,
                headers={
                    "Authorization": f"Bearer {FISH_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "reference_id": FISH_VOICE_ID,
                    "format": "mp3",
                },
            )
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

async def generate_response(
    text: str,
    client: anthropic.AsyncAnthropic,
    task_mgr: ClaudeTaskManager,
    projects: list[dict],
    conversation_history: list[dict],
    last_response: str = "",
    session_summary: str = "",
) -> str:
    """Generate a JARVIS response using Anthropic API."""
    now = datetime.now()
    current_time = now.strftime("%A, %B %d, %Y at %I:%M %p")

    # Use cached weather
    weather_info = _ctx_cache.get("weather", "Weather data unavailable.")

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
    static_system = JARVIS_SYSTEM_PROMPT.format(
        user_name=USER_NAME,
        project_dir=PROJECT_DIR,
        personal_context=personal_context,
    )

    # Dynamic block — live context that varies request-to-request. Not cached.
    dynamic_system = JARVIS_DYNAMIC_CONTEXT.format(
        current_time=current_time,
        weather_info=weather_info,
        screen_context=screen_ctx or "Not checked yet.",
        calendar_context=calendar_ctx,
        mail_context=mail_ctx,
        active_tasks=task_mgr.get_active_tasks_summary(),
        dispatch_context=dispatch_registry.format_for_prompt(),
        known_projects=format_projects_for_prompt(projects),
    )
    if lookup_status:
        dynamic_system += f"\n\nACTIVE LOOKUPS:\n{lookup_status}\nIf asked about progress, report this status."

    # Inject relevant memories and tasks
    memory_ctx = build_memory_context(text)
    if memory_ctx:
        dynamic_system += f"\n\nJARVIS MEMORY:\n{memory_ctx}"

    # Three-tier memory — inject rolling summary of earlier conversation
    if session_summary:
        dynamic_system += f"\n\nSESSION CONTEXT (earlier in this conversation):\n{session_summary}"

    # Self-awareness — remind JARVIS of last response to avoid repetition
    if last_response:
        dynamic_system += f'\n\nYOUR LAST RESPONSE (do not repeat this):\n"{last_response[:150]}"'

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

            # Weather — refresh every loop (30s is fine, API is fast)
            try:
                import urllib.request, json as _json
                url = "https://api.open-meteo.com/v1/forecast?latitude=27.77&longitude=-82.64&current=temperature_2m,weathercode&temperature_unit=fahrenheit"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    d = _json.loads(resp.read()).get("current", {})
                    temp = d.get("temperature_2m", "?")
                    _ctx_cache["weather"] = f"Current weather in St. Petersburg, FL: {temp}°F"
            except Exception:
                pass

            # Calendar — refresh today's events so JARVIS always knows the schedule.
            # Without this, the system prompt forever reads "No calendar data yet"
            # and JARVIS tells the user calendar isn't connected.
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


@asynccontextmanager
async def lifespan(application: FastAPI):
    global anthropic_client, cached_projects
    if ANTHROPIC_API_KEY:
        # max_retries=1 (default is 2): on 429 we want to surface the error
        # quickly instead of waiting through two exponential-backoff retries
        # (which is what caused those 10-15s response stalls during heavy use).
        anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=1, timeout=20.0)
    else:
        log.warning("ANTHROPIC_API_KEY not set — LLM features disabled")
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

    log.info("JARVIS server starting")

    yield


app = FastAPI(title="JARVIS Server", version="0.1.0", lifespan=lifespan)

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
    return {"status": "online", "name": "JARVIS", "version": "0.1.0"}


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


@app.get("/api/projects")
async def api_list_projects():
    global cached_projects
    cached_projects = await scan_projects()
    return {"projects": cached_projects}


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

# Start-design intent — "let's design X" / "design a Y" / "spec X" / "plan a Z".
# Captures the topic so we can spawn a session with it immediately.
_START_DESIGN_PATTERN = _action_re.compile(
    r'^\s*(?:let\'?s |let us |i (?:want to|wanna|wish to) |can we |please )?'
    r'(?:design|spec|architect|plan|think through|prototype)\s+'
    r'(?:a |an |the |some )?(?P<topic>[\w .,\'\-]+?)\s*\??\.?\s*$',
    _action_re.IGNORECASE,
)

_MERGE_BRANCH_PHRASES = {
    "merge it", "merge this", "merge the branch", "merge that branch",
    "okay merge it", "ok merge it", "go ahead and merge", "let's merge it",
    "lets merge it",
}
_RESTART_SELF_PHRASES = {
    "restart yourself", "restart jarvis", "kick yourself", "reboot yourself",
    "bounce yourself", "restart the server", "kick the server",
}

# In-design fast-action phrases — only matched when a session is active.
_SHIP_DESIGN_PHRASES = {
    "ship it", "ship this", "send it", "ok build it", "okay build it",
    "go ahead and build", "okay ship", "ok ship", "ship the design",
    "let's ship it", "lets ship it",
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
    "cursor", "chrome", "google chrome", "firefox", "safari", "terminal",
    "iterm", "iterm2", "warp", "vscode", "visual studio code", "code",
    "finder", "slack", "spotify", "notes", "mail", "messages", "calendar",
    "discord", "zoom", "obsidian", "xcode", "settings", "system settings",
    "desktop", "downloads", "documents", "music", "photos", "preview",
}


def detect_action_fast(text: str, ws=None) -> dict | None:
    """Keyword/regex-based action detection — ONLY for short, obvious commands.

    Everything else goes to the LLM which uses [ACTION:X] tags when it decides
    to act based on conversational understanding.

    When `ws` is provided AND has an active design session, design-mode
    fast-actions (ship/scrap/show-draft) are enabled. Outside a design session
    those phrases are passed through to the normal pipeline.
    """
    t = text.lower().strip()
    words = t.split()

    # Only trigger on SHORT, clear commands (< 12 words)
    if len(words) > 12:
        return None  # Long messages are conversation, not commands

    # ── Design-mode commands (only when a session is active on this ws) ──
    if ws is not None:
        import design_partner
        if design_partner.get_for_ws(ws) is not None:
            if t in _SHIP_DESIGN_PHRASES or any(t.startswith(p + " ") for p in _SHIP_DESIGN_PHRASES):
                return {"action": "ship_design"}
            if t in _SCRAP_DESIGN_PHRASES or any(t.startswith(p + " ") for p in _SCRAP_DESIGN_PHRASES):
                return {"action": "scrap_design"}
            if t in _SHOW_DRAFT_PHRASES or any(t.startswith(p + " ") for p in _SHOW_DRAFT_PHRASES):
                return {"action": "show_draft"}

    # Start-design intent — match against ORIGINAL text (preserves capitalized topic words)
    m = _START_DESIGN_PATTERN.match(text.strip())
    if m:
        topic = m.group("topic").strip()
        # Filter out single-word topics that are likely other intents misrouting
        # (e.g. "plan tomorrow" should hit calendar planning, not design).
        if topic and topic.lower() not in {"tomorrow", "today", "this", "that", "it", "something"}:
            return {"action": "start_design", "target": topic}

    # Close / dismiss the process panel. Fast-path so JARVIS responds
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
    # Skipped when the captured name is a known app (so "open Cursor" still routes
    # through the LLM's OPEN_APP path).
    for pat in _OPEN_PROJECT_PATTERNS:
        m = pat.match(t)
        if m:
            name = m.group("name").strip()
            if name and name not in _OPEN_APP_NAMES:
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
        prompt_file = Path(path) / ".jarvis_prompt.txt"
        prompt_file.write_text(target)
        await emit_step(task_id, "Prompt staged for Claude Code")

        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "cd {path} && cat .jarvis_prompt.txt | claude -p --dangerously-skip-permissions"\n'
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

# Track active lookups so JARVIS can report status
_active_lookups: dict[str, dict] = {}  # id -> {"type": str, "status": str, "started": float}


async def _lookup_and_report(lookup_type: str, lookup_fn, ws, history: list[dict] = None, voice_state: dict = None):
    """Run a slow lookup, then speak the result back.

    JARVIS stays conversational — this runs completely off the main path.
    """
    lookup_id = str(uuid.uuid4())[:8]
    _active_lookups[lookup_id] = {
        "type": lookup_type,
        "status": "working",
        "started": time.time(),
    }

    try:
        # Run the async lookup directly — these functions already use
        # asyncio.create_subprocess_exec so they don't block the event loop
        result_text = await asyncio.wait_for(
            lookup_fn(),
            timeout=30,
        )

        _active_lookups[lookup_id]["status"] = "done"

        # Speak the result — skip audio if user spoke recently to avoid collision
        if voice_state and time.time() - voice_state["last_user_time"] < 3:
            log.info(f"Skipping lookup audio for {lookup_type} — user spoke recently")
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

        # Store lookup result in conversation history so JARVIS remembers it
        if history is not None:
            history.append({"role": "assistant", "content": f"[{lookup_type} check]: {result_text}"})

    except asyncio.TimeoutError:
        _active_lookups[lookup_id]["status"] = "timeout"
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
        log.warning(f"Lookup {lookup_type} failed: {e}")
    finally:
        # Clean up after 60s
        await asyncio.sleep(60)
        _active_lookups.pop(lookup_id, None)


async def _do_calendar_lookup() -> str:
    """Slow calendar fetch — runs in thread."""
    await refresh_calendar_cache()
    events = await get_todays_events()
    if events:
        _ctx_cache["calendar"] = format_events_for_context(events)
    return format_schedule_summary(events)


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
    """Screen describe — runs in thread."""
    if anthropic_client:
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

    # Audio collision prevention — track when user last spoke
    voice_state = {"last_user_time": 0.0}

    # Self-awareness — track last spoken response to avoid repetition
    last_jarvis_response = ""

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
                        log.info(f"JARVIS: {greeting}")
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

            # ── Fix-self: activate work mode in JARVIS repo ──
            if msg.get("type") == "fix_self":
                jarvis_dir = str(Path(__file__).parent)
                await work_session.start(jarvis_dir)
                response_text = "Work mode active in my own repo, sir. Tell me what needs fixing."
                tts = strip_markdown_for_tts(response_text)
                await ws.send_json({"type": "status", "state": "speaking"})
                audio = await synthesize_speech(tts)
                if audio:
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": response_text})
                else:
                    await ws.send_json({"type": "text", "text": response_text})
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

            # Mute action routing while native research is in flight. Without
            # this, ambient transcripts (TV, conversation) get dispatched to
            # the LLM mid-research and Jarvis tries to "answer" them — which
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
                        log.info(f"JARVIS: {cancel_msg}")
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

                # ── WORK MODE: speech → claude -p → Haiku summary → JARVIS voice ──
                elif work_session.active:
                    if is_casual_question(user_text):
                        # Quick chat — bypass claude -p, use Haiku
                        response_text = await generate_response(
                            user_text, anthropic_client, task_manager,
                            cached_projects, history,
                            last_response=last_jarvis_response,
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
                                        f"You are JARVIS reporting to the user ({USER_NAME}). Summarize what happened in 1-2 sentences. "
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

                    # close_panel is handled silently before any TTS — JARVIS
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
                        if action["action"] == "open_terminal":
                            response_text = await handle_open_terminal()
                        elif action["action"] == "show_recent":
                            response_text = await handle_show_recent()
                        elif action["action"] == "describe_screen":
                            response_text = "Taking a look now, sir."
                            asyncio.create_task(_lookup_and_report("screen", _do_screen_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_calendar":
                            response_text = "Checking your calendar now, sir."
                            asyncio.create_task(_lookup_and_report("calendar", _do_calendar_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_mail":
                            response_text = "Checking your inbox now, sir."
                            asyncio.create_task(_lookup_and_report("mail", _do_mail_lookup, ws, history=history, voice_state=voice_state))
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
                            asyncio.create_task(_execute_start_design(topic, ws))
                        elif action["action"] == "ship_design":
                            response_text = ""
                            asyncio.create_task(_execute_ship_design(ws))
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
                                last_response=last_jarvis_response,
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

                                if embedded_action["action"] == "build":
                                    # Build in background — JARVIS stays conversational
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
                                    asyncio.create_task(delete_file(embedded_action["target"]))
                                elif embedded_action["action"] == "applescript":
                                    asyncio.create_task(run_applescript(embedded_action["target"]))
                                elif embedded_action["action"] == "type":
                                    asyncio.create_task(_execute_type(embedded_action["target"], press_enter=False))
                                elif embedded_action["action"] == "send":
                                    asyncio.create_task(_execute_type(embedded_action["target"], press_enter=True))
                                elif embedded_action["action"] == "create_event":
                                    asyncio.create_task(_execute_create_event(embedded_action["target"], ws))
                                elif embedded_action["action"] == "cancel_event":
                                    asyncio.create_task(_execute_cancel_event(embedded_action["target"], ws))
                                elif embedded_action["action"] == "check_date":
                                    asyncio.create_task(_execute_check_date(embedded_action["target"], ws))
                                elif embedded_action["action"] == "draft_email":
                                    asyncio.create_task(_execute_draft_email(embedded_action["target"], ws))
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
                                        asyncio.create_task(create_apple_note("JARVIS Note", target))
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
                # own _speak() call, e.g. design-partner branch).
                if response_text:
                    tts = strip_markdown_for_tts(response_text)
                    await ws.send_json({"type": "status", "state": "speaking"})
                    audio = await synthesize_speech(tts)
                    if audio:
                        await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": response_text})
                    else:
                        await ws.send_json({"type": "text", "text": response_text})
                        await ws.send_json({"type": "status", "state": "idle"})
                    log.info(f"JARVIS: {response_text}")
                    last_jarvis_response = response_text
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


# ---------------------------------------------------------------------------
# Settings / Configuration endpoints
# ---------------------------------------------------------------------------

def _env_file_path() -> Path:
    return Path(__file__).parent / ".env"

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

@app.post("/api/settings/keys")
async def api_settings_keys(body: KeyUpdate):
    allowed = {"ANTHROPIC_API_KEY", "FISH_API_KEY", "FISH_VOICE_ID", "USER_NAME", "HONORIFIC", "CALENDAR_ACCOUNTS", "DATE_OF_BIRTH", "ADDRESS", "WORK_EMAIL", "PERSONAL_EMAIL"}
    if body.key_name not in allowed:
        return JSONResponse({"success": False, "error": "Invalid key name"}, status_code=400)
    _write_env_key(body.key_name, body.key_value)
    return {"success": True}

@app.post("/api/settings/test-anthropic")
async def api_test_anthropic(body: KeyTest):
    key = body.key_value or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return {"valid": False, "error": "No key provided"}
    try:
        client = anthropic.AsyncAnthropic(api_key=key)
        await client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=10, messages=[{"role": "user", "content": "Hi"}])
        return {"valid": True}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}

@app.post("/api/settings/test-fish")
async def api_test_fish(body: KeyTest):
    key = body.key_value or os.getenv("FISH_API_KEY", "")
    if not key:
        return {"valid": False, "error": "No key provided"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.fish.audio/v1/tts",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"text": "test", "reference_id": FISH_VOICE_ID},
            )
            if resp.status_code in (200, 201):
                return {"valid": True}
            elif resp.status_code == 401:
                return {"valid": False, "error": "Invalid API key"}
            else:
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}

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
            "anthropic": bool(env_dict.get("ANTHROPIC_API_KEY", "").strip() and env_dict.get("ANTHROPIC_API_KEY", "") != "your-anthropic-api-key-here"),
            "fish_audio": bool(env_dict.get("FISH_API_KEY", "").strip() and env_dict.get("FISH_API_KEY", "") != "your-fish-audio-api-key-here"),
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


@app.post("/api/settings/bio/regenerate")
async def api_regenerate_bio():
    """Synthesize a fresh user-profile summary from accumulated notes.

    JARVIS pulls bio_notes (voice-added) and high-importance facts, then asks
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
                "message": "No notes available yet — JARVIS needs more conversations to write a profile."}

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
                "You are JARVIS writing a private dossier on the user you serve. "
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
    name = env_dict.get("ASSISTANT_NAME", "").strip() or "jarvis"
    return {"assistant_name": name}

# ---------------------------------------------------------------------------
# Control endpoints (restart, fix-self)
# ---------------------------------------------------------------------------

@app.post("/api/restart")
async def api_restart():
    """Restart the JARVIS server."""
    log.info("Restart requested — shutting down in 2 seconds")
    async def _restart():
        await asyncio.sleep(2)
        cmd = [sys.executable, __file__, "--port", "8340", "--host", "0.0.0.0"]
        os.execv(sys.executable, cmd)
    asyncio.create_task(_restart())
    return {"status": "restarting"}


@app.post("/api/fix-self")
async def api_fix_self():
    """Enter work mode in the JARVIS repo — JARVIS can now fix himself."""
    jarvis_dir = str(Path(__file__).parent)
    # The work_session is per-WebSocket, so we set a flag that the handler picks up
    # For now, also open Terminal so user can see
    script = (
        'tell application "Terminal"\n'
        '    activate\n'
        f'    do script "cd {jarvis_dir} && claude --dangerously-skip-permissions"\n'
        'end tell'
    )
    await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    log.info("Work mode: JARVIS repo opened for self-improvement")
    return {"status": "work_mode_active", "path": jarvis_dir}


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

    parser = argparse.ArgumentParser(description="JARVIS Server")
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
    print("  J.A.R.V.I.S. Server v0.1.0")
    print(f"  WebSocket: {ws_proto}://{args.host}:{args.port}/ws/voice")
    print(f"  REST API:  {proto}://{args.host}:{args.port}/api/")
    print(f"  Tasks:     {proto}://{args.host}:{args.port}/api/tasks")
    print()

    ssl_kwargs = {}
    if use_ssl:
        ssl_kwargs["ssl_keyfile"] = str(key_file)
        ssl_kwargs["ssl_certfile"] = str(cert_file)

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        **ssl_kwargs,
    )
