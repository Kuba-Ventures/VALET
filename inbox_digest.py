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


def _signed_in_gmail(obs: dict) -> bool:
    """True on any signed-in Gmail page (list OR open conversation). Gmail's
    persistent left nav + search box mean `_is_gmail_inbox` fires in both views —
    it really answers 'signed in?', which is all we use it for here."""
    import agent_loop
    return agent_loop._is_gmail_inbox(obs)


# Controls that appear ONLY inside an open conversation, never on the pure thread
# list — the reliable signal for "a message is open." Gmail's list hover-actions
# (archive/snooze) aren't in the static AX snapshot, so these don't false-fire on
# the list. (Targets the default full-width list, no reading/preview pane.)
_MSG_VIEW_HINTS = (
    "reply all", "forward", "report spam", "show details", "add to tasks",
    "print all", "show trimmed content", "to me",
)


def _is_message_view(obs: dict) -> bool:
    blob = " ".join(_label(e) for e in (obs.get("elements") or [])).lower()
    return any(h in blob for h in _MSG_VIEW_HINTS)


def _on_list(obs: dict) -> bool:
    """True when we're on the thread LIST (signed-in Gmail, no conversation open)."""
    return _signed_in_gmail(obs) and not _is_message_view(obs)


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


async def _enumerate_today(client, obs: dict, cap: int, today_str: str) -> dict:
    """Ask the model to read the inbox element list and return TODAY's conversation
    rows, top to bottom. Returns {"rows": [{"sender","subject"}...], "has_more": bool}.

    Model-driven on purpose: Gmail's row labels vary (unread markers, categories,
    attachments), and the model reading the labels is far more robust than a rigid
    parser. Fails soft to an empty list."""
    if not client:
        return {"rows": [], "has_more": False}
    elements_txt = perception.elements_as_text(obs.get("elements", []), limit=120)
    system = (
        "You are reading a Gmail inbox's accessibility element list. Return the "
        "conversation-list rows RECEIVED TODAY, top to bottom.\n"
        f"Today is {today_str}. In Gmail's list, a row received today shows a "
        "CLOCK TIME in its date column (e.g. '2:47 PM', '9:03 AM'); a row from an "
        "earlier day shows a DATE ('Jul 15', 'Nov 3'). Keep ONLY rows whose date "
        "token is a time of day — those are today's.\n"
        "Ignore the compose button, search box, category tabs (Primary/Social/"
        "Promotions), labels, nav, and footer — only real message rows.\n"
        f"Return AT MOST {cap} rows. Reply with STRICT JSON only:\n"
        '{"rows":[{"sender":"...","subject":"..."}],"has_more":false}\n'
        f'Set "has_more" true if there are MORE than {cap} rows received today '
        "(so we can tell the user we capped)."
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": f"Inbox elements:\n{elements_txt}"}],
        )
        data = _parse_json(resp.content[0].text)
        if not isinstance(data, dict):
            return {"rows": [], "has_more": False}
        rows = [
            {"sender": (r.get("sender") or "").strip(),
             "subject": (r.get("subject") or "").strip()}
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


async def _go_back_to_inbox(executor, ax_executor, app: Optional[str]) -> bool:
    """Return from an open conversation to the inbox and CONFIRM we're there before
    the caller clicks the next row (the fragile part per the issue). Opening a Gmail
    conversation pushes a `#inbox/<id>` history entry, so browser-Back (Cmd+[)
    reliably pops back to the thread list. Verified by re-observation; one retry via
    a 'Back to Inbox' control if Back didn't land."""
    for _attempt in range(2):
        await ax_executor.key_combo("cmd+[", app=app)  # Chrome: history back → #inbox
        await asyncio.sleep(_BACK_SETTLE)
        obs = await perception.build_observation(executor, app=app)
        if _on_list(obs):
            return True
        # Back didn't land — try an explicit "Back to inbox" control before giving up.
        for e in obs.get("elements", []) or []:
            lab = _norm(_label(e))
            fr = e.get("frame")
            if fr and len(fr) == 4 and (
                    "back to inbox" in lab or lab == "inbox"
                    or lab.startswith("inbox ") and len(lab) < 20):  # "Inbox 3" (unread count)
                cx, cy = fr[0] + fr[2] / 2.0, fr[1] + fr[3] / 2.0
                await ax_executor.click_element(point=(cx, cy), app=app)
                await asyncio.sleep(_BACK_SETTLE)
                obs = await perception.build_observation(executor, app=app)
                if _on_list(obs):
                    return True
                break
    return False


async def run_digest(executor, ax_executor, client, *,
                     emit: Optional[EmitFn] = None,
                     kill_switch=None, cap: int = _DEFAULT_CAP,
                     today_str: str = "today") -> dict:
    """Read every message received today in the OPEN Gmail inbox and return one
    summary across all of them.

    Preconditions: Gmail is open and signed in (issue #284 owns the login). If the
    focused window isn't a Gmail inbox we say so rather than guessing.

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
        # 1) Observe the inbox and confirm we're actually on it. Chrome builds its
        #    full AX tree lazily, so a first snapshot can miss Gmail's chrome — give
        #    it one retry before concluding Gmail isn't up.
        await _emit("Reading your inbox…", status="active")
        obs = await perception.build_observation(executor)
        app = obs.get("app")
        if _is_browser(app) and not _signed_in_gmail(obs):
            await asyncio.sleep(0.6)
            obs = await perception.build_observation(executor, app=app)
        if not _is_browser(app) or not _signed_in_gmail(obs):
            return {"status": "no_inbox", "count": 0, "capped": False,
                    "summary": "I need your Gmail inbox open and signed in first, sir."}
        # Started on an open conversation? Step back to the list before enumerating.
        if _is_message_view(obs):
            await _go_back_to_inbox(executor, ax_executor, app)
            obs = await perception.build_observation(executor, app=app)

        # 2) Enumerate today's messages (bounded).
        enum = await _enumerate_today(client, obs, cap, today_str)
        targets = enum["rows"]
        capped = enum["has_more"]
        if not targets:
            return {"status": "empty", "count": 0, "capped": False,
                    "summary": "Nothing new in your inbox today, sir."}
        total = len(targets)
        await _emit(f"Found {total} today{' (capped)' if capped else ''}",
                    detail="reading each in turn", status="active")

        # 3) Open each in turn → read → back to inbox. Re-observe every iteration so
        #    stale refs never bite; dedupe by on-screen position so we can't loop on
        #    one row or read the same one twice.
        collected: list = []
        used: set = set()
        for i, tgt in enumerate(targets, 1):
            if _halted():
                break
            await _emit(f"Reading {i} of {total}…",
                        detail=(tgt.get("subject") or tgt.get("sender") or "")[:80],
                        status="active")

            obs = await perception.build_observation(executor, app=app)
            if not _on_list(obs):
                # Lost the list (unexpected) — one recovery attempt, else stop clean.
                if not await _go_back_to_inbox(executor, ax_executor, app):
                    await _emit("Lost the inbox view", status="error")
                    break
                obs = await perception.build_observation(executor, app=app)

            row = _find_row_element(obs, tgt.get("sender", ""), tgt.get("subject", ""), used)
            if not row:
                # Row scrolled off / relabeled — skip it honestly rather than guess.
                await _emit(f"Couldn't find email {i} on screen — skipping",
                            status="active")
                continue
            fr = row["frame"]
            used.add(_frame_sig(fr))
            cx, cy = fr[0] + fr[2] / 2.0, fr[1] + fr[3] / 2.0

            if _halted():
                break
            await ax_executor.click_element(point=(cx, cy), app=app)
            await asyncio.sleep(_OPEN_SETTLE)

            msg_obs = await perception.build_observation(executor, app=app)
            if not _is_message_view(msg_obs):
                # The click didn't open the conversation — don't record a list
                # snapshot as if it were the email.
                await _emit(f"Email {i} didn't open — skipping", status="active")
                continue
            collected.append({
                "sender": tgt.get("sender", ""),
                "subject": tgt.get("subject", ""),
                "body": _visible_text(msg_obs),
            })
            await _emit(f"Read {i} of {total}",
                        detail=(tgt.get("subject") or "")[:80], status="done")

            # Back to the inbox before the next row (verified).
            if i < total and not _halted():
                if not await _go_back_to_inbox(executor, ax_executor, app):
                    await _emit("Couldn't return to the inbox — stopping here",
                                status="error")
                    break

        if _halted() and not collected:
            return {"status": "halted", "count": 0, "capped": capped,
                    "summary": "Halted, sir."}

        # 4) One summary across everything collected.
        await _emit("Summarizing today's mail…", status="active")
        read_n = len(collected)
        total_hint = (f"{read_n} email(s) received today."
                      + (" (More arrived than were read — this is the recent batch.)"
                         if capped else ""))
        summary = await _aggregate(client, collected, total_hint)
        # State the cap deterministically (issue: never silently truncate) rather
        # than trusting the model to have mentioned it.
        if capped:
            summary = (summary.rstrip() + f" That's the {read_n} most recent, sir — "
                       "more came in today than I read.")
        await _emit(f"Digest ready — {read_n} email(s)", status="done")
        return {"status": "done", "count": read_n, "capped": capped, "summary": summary}
    except Exception as e:
        log.error("inbox digest failed: %s", e)
        return {"status": "error", "count": 0, "capped": False,
                "summary": "Something went wrong reading your inbox, sir."}
