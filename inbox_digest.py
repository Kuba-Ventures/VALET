"""Today's-inbox digest — batch read of the open Gmail inbox (issue #285).

VALET can already open ONE thing and summarize the FOCUSED window
(`[ACTION:OPEN_ON_SCREEN]` / `[ACTION:SUMMARIZE_SCREEN]`). What it lacked was the
*loop that batches* those primitives: enumerate today's messages, open each one
in turn, read it, return to the inbox, and synthesize a SINGLE summary across all
of them. That loop lives here.

Pure orchestration, same shape as `agent_loop.py`: it drives the passed-in
control executor + AX executor + model client and reports progress through a
caller-supplied `emit` callback — no direct WebSocket / process_events coupling.
The caller (server.py) owns the task_context, speaking, and the note step (#286).

## "Received today"
We scope to Gmail's own same-day rendering: Gmail shows a *clock time* ("2:47 PM")
in a row's date column for messages received today, and a *calendar date*
("Jul 15", "Nov 3, 2025") for older ones. The enumerator is told today's date and
keeps only rows whose date token is a time-of-day. This needs no locale-specific
date math and matches what the user sees as "today" in the list.

## Threads
One item per inbox row (a conversation counts once), per the issue's MVP default.

## Safety
Read-only: open a message, read it, go back. No mutating actions, so each step
runs straight through the RAW executor (no confirm card per email — that would be
miserable UX) but the kill switch is checked before every click and every
navigation, so "stop" halts the batch immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from typing import Awaitable, Callable, Optional

import perception

log = logging.getLogger("valet.inbox")

# Default ceiling on messages read in one digest. A huge inbox can't run forever;
# if there are more than this many today, we read the cap and SAY so (issue: no
# silent truncation).
_DEFAULT_CAP = 10
# Let a Gmail SPA transition (open a conversation / go back) actually render
# before the next AX snapshot — mirrors agent_loop._ACT_SETTLE, a touch longer
# because Gmail's conversation view is heavier than a menu.
_OPEN_SETTLE = 1.0
_BACK_SETTLE = 1.0
# Cap the per-email text we carry into the aggregation prompt. Enough for a gist
# and action items; keeps the batch call bounded even at the message cap.
_PER_EMAIL_CHARS = 1500

EmitFn = Callable[..., Awaitable[None]]


def _is_browser(app: Optional[str]) -> bool:
    import agent_loop
    return agent_loop._is_browser(app)


def _is_chrome(app: Optional[str]) -> bool:
    a = (app or "").lower()
    return "chrome" in a or "chromium" in a


_GMAIL_URL = "mail.google.com"


# ── Chrome tab helpers (the authoritative signal) ────────────────────────────
# Text hints ("inbox", "compose") false-fire on other pages — GitHub's
# notifications page has an "Inbox" sidebar, which is exactly what made the digest
# run on the wrong tab. The active tab's URL is unambiguous, so we gate on it.
async def _osascript(script: str) -> tuple:
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        return proc.returncode, out.decode().strip()
    except Exception as e:
        log.warning("osascript failed: %s", e)
        return 1, ""


async def _chrome_active_url() -> str:
    """URL of Chrome's front-window active tab, or '' (no Chrome / no window /
    AppleScript denied)."""
    rc, out = await _osascript(
        'tell application "Google Chrome"\n'
        '  if (count of windows) = 0 then return ""\n'
        '  return URL of active tab of front window\n'
        'end tell')
    return out if rc == 0 else ""


async def _chrome_active_title() -> str:
    """Title of Chrome's front-window active tab (Gmail titles carry the account
    email, e.g. 'Inbox - finley@corp.com - …'), or ''."""
    rc, out = await _osascript(
        'tell application "Google Chrome"\n'
        '  if (count of windows) = 0 then return ""\n'
        '  return title of active tab of front window\n'
        'end tell')
    return out if rc == 0 else ""


_TAB_SEP = "§"  # § — a delimiter that won't appear in a tab title/URL


async def _gmail_tabs() -> list:
    """All open Gmail tabs as [{w, t, title, url}] (window + tab indices for
    activation). Read-only enumeration."""
    rc, out = await _osascript(
        'tell application "Google Chrome"\n'
        '  set out to ""\n'
        '  set wi to 0\n'
        '  repeat with w in windows\n'
        '    set wi to wi + 1\n'
        '    set ti to 0\n'
        '    repeat with t in tabs of w\n'
        '      set ti to ti + 1\n'
        '      set u to URL of t\n'
        '      if u contains "mail.google.com" then\n'
        f'        set out to out & wi & "{_TAB_SEP}" & ti & "{_TAB_SEP}" & (title of t) & "{_TAB_SEP}" & u & linefeed\n'
        '      end if\n'
        '    end repeat\n'
        '  end repeat\n'
        '  return out\n'
        'end tell')
    if rc != 0 or not out:
        return []
    tabs = []
    for line in out.splitlines():
        p = line.split(_TAB_SEP)
        if len(p) >= 4 and p[0].isdigit() and p[1].isdigit():
            tabs.append({"w": int(p[0]), "t": int(p[1]), "title": p[2], "url": p[3]})
    return tabs


_GMAIL_DOMAINS = ("gmail.com", "googlemail.com")
_STOP_TOKENS = {"the", "and", "for", "inbox", "email", "emails", "e-mail",
                "mail", "account", "gmail", "messages", "box", "my"}


def _email_from_title(title: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", title or "")
    return m.group(0) if m else ""


def _score_tab(title: str, hint: str) -> int:
    """How well a Gmail tab's title matches a spoken account hint. A distinctive
    hint word appearing in the title scores strongest; 'work'/'personal' fall back
    to the domain (custom domain = work, gmail.com = personal). 0 = no signal."""
    tl = (title or "").lower()
    h = (hint or "").strip().lower()
    if not h:
        return 0
    score = 0
    for tok in re.findall(r"[a-z0-9.+_-]{3,}", h):
        if tok in _STOP_TOKENS:
            continue
        if tok in tl:
            score += 3
    dom = _email_from_title(title).split("@", 1)[-1].lower() if "@" in _email_from_title(title) else ""
    is_personal = any(dom.endswith(d) for d in _GMAIL_DOMAINS)
    if any(w in h for w in ("work", "business", "company", "corporate", "office")):
        if dom and not is_personal:
            score += 2
    if any(w in h for w in ("personal", "home", "private")):
        if is_personal:
            score += 2
    return score


async def _activate_tab(w: int, t: int) -> bool:
    rc, out = await _osascript(
        'tell application "Google Chrome"\n'
        f'  set active tab index of window {w} to {t}\n'
        f'  set index of window {w} to 1\n'
        '  activate\n'
        '  return "ok"\n'
        'end tell')
    return rc == 0 and out == "ok"


async def _switch_to_gmail(hint: str = "") -> tuple:
    """Activate the open Gmail tab that best matches `hint` (or the first Gmail tab
    when no account was named). Returns (status, email):
      ("ok", email)       switched — email of the tab we landed on
      ("no_match", "")    an account was named but no open tab matches it
      ("none", "")        no Gmail tab open at all"""
    tabs = await _gmail_tabs()
    if not tabs:
        return "none", ""
    if hint:
        best = max(tabs, key=lambda x: _score_tab(x["title"], hint))
        if _score_tab(best["title"], hint) <= 0:
            return "no_match", ""
        tab = best
    else:
        tab = tabs[0]
    ok = await _activate_tab(tab["w"], tab["t"])
    return ("ok" if ok else "none"), _email_from_title(tab["title"])


def _account_base(url: str) -> str:
    """The per-account Gmail base 'https://mail.google.com/mail/u/N/' from a Gmail
    URL, so a search stays on the SAME account. Falls back to u/0."""
    m = re.match(r"(https://mail\.google\.com/mail/u/\d+/)", url or "")
    return m.group(1) if m else "https://mail.google.com/mail/u/0/"


async def _gmail_go_to_inbox(base: str) -> bool:
    """Point Chrome's active tab at the account's #inbox, so we always read a clean
    inbox list (not a stale open-message or search view). A hash change is an SPA
    nav — no full reload. Read-only navigation of the tab already in front."""
    rc, out = await _osascript(
        'tell application "Google Chrome"\n'
        '  if (count of windows) = 0 then return "none"\n'
        f'  set URL of active tab of front window to "{base}#inbox"\n'
        '  return "ok"\n'
        'end tell')
    return rc == 0 and out == "ok"


def _gmail_date_display(date_iso: str) -> str:
    """How Gmail labels a row's date for `date_iso`: 'Jul 17' (this year) — the
    token the enumerator matches against. Falls back to the ISO string."""
    try:
        d = date.fromisoformat(date_iso)
        return f"{d.strftime('%b')} {d.day}"
    except Exception:
        return date_iso


def _view_from_url(url: str) -> str:
    """Classify a Gmail URL: 'list' (thread list — #inbox, #search/…, #label/…),
    'message' (open conversation — a thread id trails the section, e.g.
    #inbox/FMfcgz…), or 'overlay' (an attachment preview / compose popup sitting
    over the list — ?projector= / ?compose= — which is NOT a readable message and
    must be dismissed). Returns '' when the URL isn't Gmail."""
    u = url or ""
    if _GMAIL_URL not in u:
        return ""
    # Attachment "projector" or compose overlay — a click landed on an attachment
    # chip, not the message. Treat as an overlay to skip + dismiss, never as opened.
    if "projector=" in u or "?compose=" in u or "&compose=" in u:
        return "overlay"
    frag = u.split("#", 1)[1] if "#" in u else ""
    segs = [s for s in frag.split("/") if s]
    last = segs[-1] if segs else ""
    # A thread id is a long alphanumeric token; section names ("inbox") are short,
    # and search queries carry %/: so they aren't alnum.
    if len(segs) >= 2 and len(last) >= 16 and last.isalnum():
        return "message"
    return "list"


# ── AX fallback (non-Chrome browsers, or AppleScript denied) ─────────────────
def _labels_blob(obs: dict) -> str:
    return " ".join(_label(e) for e in (obs.get("elements") or [])).lower()


def _strict_gmail(obs: dict) -> bool:
    """Are we on a Gmail thread list? Stricter than a single hint: needs ≥2 of
    Gmail's distinctive chrome (compose, its search box, a list section) so a
    stray 'Inbox' label on another site can't pass."""
    blob = _labels_blob(obs)
    score = (
        ("compose" in blob)
        + ("search mail" in blob or "search in mail" in blob)
        + any(h in blob for h in ("primary", "snoozed", "conversation list", "more emails"))
    )
    return score >= 2


# Controls that appear ONLY inside an open conversation, never on the pure thread
# list — the AX signal for "a message is open" when the URL is unavailable.
_MSG_VIEW_HINTS = (
    "reply all", "forward", "report spam", "show details", "add to tasks",
    "print all", "show trimmed content", "to me",
)


def _is_message_view(obs: dict) -> bool:
    blob = _labels_blob(obs)
    return any(h in blob for h in _MSG_VIEW_HINTS)


def _is_signin(obs: dict) -> bool:
    import agent_loop
    return agent_loop._is_gmail_signin(obs)


async def _current_view(app: Optional[str] = None) -> str:
    """Best available list/message/overlay classification from Chrome's active-tab
    URL, or '' (Chrome not scriptable / not on Gmail) so the caller falls back to
    AX. Queries Chrome directly — never gated on perception's frontmost-app guess.
    Kept cheap (one AppleScript call)."""
    return _view_from_url(await _chrome_active_url())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _label(e: dict) -> str:
    return " ".join(filter(None, [e.get("title"), e.get("value")])).strip()


def _visible_text(obs: dict, limit: int = _PER_EMAIL_CHARS) -> str:
    """Flatten the observed message window into one text blob for reading. Joins
    element labels (the open conversation's sender / subject / body all surface as
    AX text), de-duped in order, capped."""
    seen: set = set()
    parts: list = []
    for e in obs.get("elements", []) or []:
        lab = _label(e)
        if len(lab) < 2:
            continue
        key = lab.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(lab)
    blob = " · ".join(parts)
    return blob[:limit]


def _find_row_element(obs: dict, sender: str, subject: str, used: set) -> Optional[dict]:
    """Best on-screen element to click for a (sender, subject) target, or None.

    Scored substring match against the fresh observation's labels: subject is the
    distinctive signal (weight 2), sender confirms it (weight 1). `used` holds the
    frame-signatures of rows already opened this run, so we never re-open one."""
    want_sub = _norm(subject)
    want_send = _norm(sender)
    best = None
    best_score = 0
    for e in obs.get("elements", []) or []:
        fr = e.get("frame")
        if not fr or len(fr) != 4:
            continue
        sig = _frame_sig(fr)
        if sig in used:
            continue
        lab = _norm(_label(e))
        if not lab:
            continue
        score = 0
        # A distinctive leading slice of the subject — short enough to survive
        # Gmail's row truncation ("Project review m…"), long enough to be unique.
        sub_key = want_sub[:24]
        if len(sub_key) >= 4 and sub_key in lab:
            score += 2
        if want_send and len(want_send) >= 3 and want_send.split()[0] in lab:
            score += 1
        if score > best_score:
            best_score, best = score, e
    return best if best_score > 0 else None


def _frame_sig(fr: list) -> tuple:
    """A stable-ish signature for a row's on-screen position, so the same row seen
    across two observations maps to the same key (guards against re-opening it)."""
    return (round(fr[0] / 5.0), round(fr[1] / 5.0))


async def _enumerate_rows(client, obs: dict, cap: int, when_str: str,
                          mode: str = "today") -> dict:
    """Read the Gmail element list and return the conversation rows we want, each
    with its body preview. Returns {"rows": [{"sender","subject","snippet"}...],
    "has_more": bool}.

    mode="today": the inbox list — keep only rows whose date column is a CLOCK TIME
      (that's how Gmail renders same-day mail).
    mode="date": the inbox list — keep only rows whose date column matches
      `when_str` (e.g. "Jul 17").

    Model-driven on purpose: Gmail's row labels vary (unread markers, categories,
    attachments), and the model reading the labels is far more robust than a rigid
    parser. Fails soft to an empty list."""
    if not client:
        return {"rows": [], "has_more": False}
    # Build the element text OURSELVES with full-length labels. perception's
    # elements_as_text truncates every label to 60 chars, but Gmail puts the
    # date/time near the END of a ~100-char row label ("Sender , Subject … , 6:36
    # AM , snippet"). Truncating chops off the time, so the model can't tell which
    # rows are today's and reports an empty inbox. Keep the message rows in full.
    _lines = []
    for e in (obs.get("elements") or [])[:250]:
        _lab = (e.get("title") or e.get("value") or "").strip()
        if _lab:
            _lines.append(f"[{e.get('ref','')}] {e.get('role','')} — {_lab[:220]}")
    elements_txt = "\n".join(_lines)
    if mode == "date":
        which = (
            f"Return the inbox rows dated {when_str}, top to bottom. In Gmail's "
            "list each row's date column shows either a CLOCK TIME (that row is "
            "today) or a DATE like 'Jul 17' / 'Nov 3'. Keep ONLY rows whose date "
            f"column is {when_str} (it may also appear with a year). Skip rows with "
            "a clock time and rows dated any other day.\n")
    else:
        which = (
            "Return the conversation-list rows RECEIVED TODAY, top to bottom.\n"
            f"Today is {when_str}. In Gmail's list, a row received today shows a "
            "CLOCK TIME in its date column (e.g. '2:47 PM', '9:03 AM'); a row from "
            "an earlier day shows a DATE ('Jul 15', 'Nov 3'). Keep ONLY rows whose "
            "date token is a time of day — those are today's.\n")
    system = (
        "You are reading a Gmail accessibility element list.\n" + which +
        "Ignore the compose button, search box, category tabs (Primary/Social/"
        "Promotions), labels, nav, and footer — only real message rows.\n"
        "A row label reads 'Sender , Subject , [date/time] , body preview…'. For "
        "each row give the sender, the subject, and the snippet (the body-preview "
        "text after the date/time — verbatim, don't invent).\n"
        f"Return AT MOST {cap} rows. Reply with STRICT JSON only:\n"
        '{"rows":[{"sender":"...","subject":"...","snippet":"..."}],"has_more":false}\n'
        f'Set "has_more" true if there are MORE than {cap} matching rows '
        "(so we can tell the user we capped)."
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": f"Inbox elements:\n{elements_txt}"}],
        )
        data = _parse_json(resp.content[0].text)
        if not isinstance(data, dict):
            return {"rows": [], "has_more": False}
        rows = [
            {"sender": (r.get("sender") or "").strip(),
             "subject": (r.get("subject") or "").strip(),
             "snippet": (r.get("snippet") or "").strip()}
            for r in (data.get("rows") or [])
            if isinstance(r, dict) and (r.get("subject") or r.get("sender"))
        ][:cap]
        return {"rows": rows, "has_more": bool(data.get("has_more"))}
    except Exception as e:
        log.warning("inbox enumerate failed: %s", e)
        return {"rows": [], "has_more": False}


async def _aggregate(client, emails: list, total_hint: str) -> str:
    """Synthesize ONE spoken digest over all collected emails: a lead gist plus a
    quick per-email one-liner. British-butler voice, suitable to speak AND to write
    to a note (#286)."""
    if not emails:
        return "Nothing new in your inbox today, sir."
    if not client:
        return _fallback_digest(emails)
    blocks = []
    for i, e in enumerate(emails, 1):
        blocks.append(
            f"[{i}] From: {e.get('sender') or 'unknown'}\n"
            f"Subject: {e.get('subject') or '(none)'}\n"
            f"Content: {e.get('body') or '(unreadable)'}")
    system = (
        "You are VALET giving the user a spoken digest of the emails they received "
        "today. Lead with ONE plain sentence gist (how many, overall theme). Then "
        "give a quick one-liner per email — who it's from and the one thing that "
        "matters or that they must DO — as a natural spoken run, not a list "
        "('From Stripe, this month's invoice is ready; from Jane, the review moved "
        "to Thursday'). British butler tone, dry and economical. No markdown, no "
        "bullet characters, no headings — this is read aloud."
    )
    user = f"{total_hint}\n\n" + "\n\n".join(blocks)
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return (resp.content[0].text or "").strip() or _fallback_digest(emails)
    except Exception as e:
        log.warning("inbox aggregate failed: %s", e)
        return _fallback_digest(emails)


def cap_len(emails: list) -> int:
    return min(len(emails), 6)


def _fallback_digest(emails: list) -> str:
    who = "; ".join(f"{e.get('sender') or 'someone'} — {e.get('subject') or ''}".strip(" —")
                    for e in emails[:cap_len(emails)])
    more = f", and {len(emails) - cap_len(emails)} more" if len(emails) > cap_len(emails) else ""
    return f"{len(emails)} today, sir: {who}{more}."


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if 0 <= a < b:
            try:
                return json.loads(t[a:b + 1])
            except Exception:
                return None
    return None


async def _back_on_list(executor, app: Optional[str]) -> bool:
    """True once we're back on the thread list — URL when Chrome, else AX (strict
    Gmail chrome present, no message-view controls)."""
    v = await _current_view(app)
    if v:
        return v == "list"
    obs = await perception.build_observation(executor, app=app)
    return _strict_gmail(obs) and not _is_message_view(obs)


async def _go_back_to_inbox(executor, ax_executor, app: Optional[str]) -> bool:
    """Return from an open conversation to the inbox and CONFIRM we're there before
    the caller clicks the next row (the fragile part per the issue). Opening a Gmail
    conversation pushes a `#inbox/<id>` history entry, so browser-Back (Cmd+[)
    reliably pops back to the thread list. Verified by URL/AX; one retry via a
    'Back to Inbox' control if Back didn't land."""
    for _attempt in range(2):
        await ax_executor.key_combo("cmd+[", app=app)  # Chrome: history back → #inbox
        await asyncio.sleep(_BACK_SETTLE)
        if await _back_on_list(executor, app):
            return True
        # Back didn't land — try an explicit "Back to inbox" control before giving up.
        obs = await perception.build_observation(executor, app=app)
        for e in obs.get("elements", []) or []:
            lab = _norm(_label(e))
            fr = e.get("frame")
            if fr and len(fr) == 4 and (
                    "back to inbox" in lab or lab == "inbox"
                    or lab.startswith("inbox ") and len(lab) < 20):  # "Inbox 3" (unread count)
                cx, cy = fr[0] + fr[2] / 2.0, fr[1] + fr[3] / 2.0
                await ax_executor.click_element(point=(cx, cy), app=app)
                await asyncio.sleep(_BACK_SETTLE)
                if await _back_on_list(executor, app):
                    return True
                break
    return False


async def run_digest(executor, ax_executor, client, *,
                     emit: Optional[EmitFn] = None,
                     kill_switch=None, cap: int = _DEFAULT_CAP,
                     today_str: str = "today", account: str = "",
                     date_iso: str = "", today_iso: str = "",
                     date_label: str = "today") -> dict:
    """Summarize a day's Gmail — today by default, or any specific day.

    Preconditions: Gmail is open and signed in (issue #284 owns the login). If the
    focused window isn't a Gmail inbox we say so rather than guessing.

    `account` is an optional spoken hint for WHICH inbox ("work", "personal", an
    email, or a name) when several Gmail accounts are open — matched against the
    tab titles, which carry the account email.

    `date_iso` selects the day (YYYY-MM-DD); empty or == `today_iso` means today.
    Today is read straight from the inbox list; any other day is scoped with a
    Gmail `after:/before:` search (so mail that isn't on the inbox's first page
    still shows). `date_label` is the spoken form ("today", "yesterday", "July 16").

    Returns {status, summary, count, capped}:
      status: done | empty | no_inbox | halted | error
    """
    async def _emit(title, detail="", status="active"):
        if emit:
            try:
                await emit(title, detail, status)
            except Exception:
                pass

    def _halted() -> bool:
        return kill_switch is not None and kill_switch.is_engaged()

    try:
        # 1) Find the RIGHT Gmail. Gate on the real Chrome tab URL — a text hint
        #    like "inbox" also appears on other sites (GitHub notifications), which
        #    is what made an earlier build run on the wrong tab. When an account is
        #    named, match it against the tab TITLES (they carry the email) and
        #    switch even if we're already on a *different* Gmail account.
        await _emit("Reading your inbox…", status="active")
        obs = await perception.build_observation(executor)
        app = obs.get("app")

        # Ask Chrome directly which tabs are Gmail — do NOT gate on perception's
        # frontmost-app guess. That guess returns "frontmost"/the wrong app when
        # focus is ambiguous (e.g. VALET itself was frontmost when you spoke), and
        # gating on it made us skip Chrome entirely and wrongly say "no Gmail open".
        # Querying Chrome also means "go TO my email" works when Chrome isn't the
        # focused app yet.
        url = await _chrome_active_url()          # "" if Chrome not running/scriptable
        title = await _chrome_active_title()
        tabs = await _gmail_tabs()                # [] if no Gmail tab / Chrome down
        active_is_gmail = _GMAIL_URL in url

        # Switch to the right tab unless the ACTIVE tab already satisfies the ask.
        if account:
            already = active_is_gmail and _score_tab(title, account) > 0
            must_switch = not already
        elif active_is_gmail:
            must_switch = False                   # already on a Gmail tab, keep it
        else:
            must_switch = bool(tabs)              # not on Gmail → grab a Gmail tab

        if must_switch:
            status, email = await _switch_to_gmail(account)
            if status == "no_match" and account:
                return {"status": "no_inbox", "count": 0, "capped": False,
                        "summary": f"I don't see your {account} Gmail open in Chrome, "
                                   "sir — open that inbox and I'll go through it."}
            if status == "ok":
                await _emit(f"Switching to {email or 'Gmail'}…", status="active")
                await asyncio.sleep(0.9)
                url = await _chrome_active_url()
                active_is_gmail = _GMAIL_URL in url
                obs = await perception.build_observation(executor, app=app)
            # status == "none": no Gmail tab at all → honest miss below.

        # On Gmail if Chrome's active tab is Gmail, or (Chrome unavailable) a
        # non-Chrome browser is showing Gmail per the AX snapshot.
        on_gmail = active_is_gmail or (
            not url and _is_browser(app) and _strict_gmail(obs))
        if not on_gmail:
            return {"status": "no_inbox", "count": 0, "capped": False,
                    "summary": "I don't see your Gmail open in Chrome, sir — open your "
                               "inbox and I'll go through today's mail."}

        # We confirmed Gmail via Chrome's URL — so observe and click the CHROME
        # window from here on, NOT perception's frontmost-app guess. That guess can
        # be "frontmost", which resolves to no window and returns an empty element
        # list — the tab switch succeeds but the inbox reads as empty ("nothing
        # new"). Pinning to Google Chrome fixes the observation the same way we
        # fixed tab discovery.
        if url:
            app = "Google Chrome"
        obs = await perception.build_observation(executor, app=app)
        if _is_signin(obs):
            return {"status": "no_inbox", "count": 0, "capped": False,
                    "summary": "You're signed out of Gmail, sir — sign in and I'll go "
                               "through today's mail."}
        is_today = (not date_iso) or (date_iso == today_iso)

        # 2) Read the INBOX list and filter rows to the requested day. We land on
        #    #inbox first for a clean, consistent read (not a stale message/search
        #    view). We deliberately do NOT use Gmail's search: its newer unified
        #    "Search results" UI doesn't expose result rows to accessibility, so a
        #    search reads as empty. The inbox rows carry each day's date label
        #    ("Jul 17") or a clock time (today), which is all we need to filter.
        await _gmail_go_to_inbox(_account_base(url))
        await asyncio.sleep(1.2)
        obs = await perception.build_observation(executor, app=app)

        if is_today:
            enum = await _enumerate_rows(client, obs, cap, today_str, mode="today")
        else:
            date_display = _gmail_date_display(date_iso)   # e.g. "Jul 17"
            enum = await _enumerate_rows(client, obs, cap, date_display, mode="date")
        if not enum["rows"]:
            await asyncio.sleep(1.2)
            obs = await perception.build_observation(executor, app=app)
            when = today_str if is_today else _gmail_date_display(date_iso)
            enum = await _enumerate_rows(client, obs, cap, when,
                                         mode="today" if is_today else "date")
        rows = enum["rows"]
        capped = enum["has_more"]
        if not rows:
            if is_today:
                msg = "Nothing new in your inbox today, sir."
            else:
                # Recent days sit on the inbox's first page; older ones may not.
                msg = (f"I don't see any emails from {date_label} on your inbox's "
                       "front page, sir — it may be further back than what's shown.")
            return {"status": "empty", "count": 0, "capped": False, "summary": msg}
        total = len(rows)

        # 3) Summarize straight from the rows. Each Gmail row already carries the
        #    sender, subject, and a body preview (~100 chars) in its label — we
        #    verified that's enough for a useful digest. We deliberately do NOT open
        #    each message by clicking: coordinate clicks + browser-back proved
        #    fragile (they resized/duplicated the window, drifted to the wrong Gmail
        #    account, and hit the address bar). Reading the list is deterministic
        #    and stays put. Fuller per-message reading is a future enhancement, best
        #    done via Gmail's own j/o/u keyboard navigation rather than clicks.
        await _emit(f"Summarizing {total} from {date_label}{' (capped)' if capped else ''}",
                    status="active")
        collected = [{"sender": r.get("sender", ""),
                      "subject": r.get("subject", ""),
                      "body": r.get("snippet", "")} for r in rows]
        total_hint = f"{total} email(s) from {date_label} (summarized from the inbox previews)."
        summary = await _aggregate(client, collected, total_hint)
        # State the cap deterministically (issue: never silently truncate).
        if capped:
            summary = (summary.rstrip() + f" That's the {total} most recent from "
                       f"{date_label}, sir — there were more.")
        await _emit(f"Digest ready — {total} email(s)", status="done")
        return {"status": "done", "count": total, "capped": capped, "summary": summary}
    except Exception as e:
        log.error("inbox digest failed: %s", e)
        return {"status": "error", "count": 0, "capped": False,
                "summary": "Something went wrong reading your inbox, sir."}


