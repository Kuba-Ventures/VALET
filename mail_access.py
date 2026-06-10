"""Gmail access — read + draft creation.

Replaces the prior AppleScript-based Apple Mail integration. Public function
signatures and return shapes are preserved so server.py callers don't need
to change. Message dicts have:

    sender (str), subject (str), date (str), read (bool), [content (str)]

create_draft() is the only write surface — it ALWAYS creates a draft. There
is no send() function on purpose; the user clicks Send themselves.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from datetime import datetime

from google_auth import gmail_service, get_connected_email

log = logging.getLogger("valet.mail")


def _header(headers: list[dict], name: str) -> str:
    target = name.lower()
    for h in headers:
        if h.get("name", "").lower() == target:
            return h.get("value", "") or ""
    return ""


def _format_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        # Use a short, voice-friendly format consistent with the old Apple Mail output.
        return dt.strftime("%a %b %-d at %-I:%M %p")
    except Exception:
        return raw


def _decode_body(payload: dict) -> str:
    """Pull a plain-text body from the message payload (recursively)."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if data and mime.startswith("text/"):
        try:
            raw = base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
            if mime == "text/html":
                # Strip tags quickly — good enough for voice context.
                raw = re.sub(r"<[^>]+>", " ", raw)
                raw = re.sub(r"\s+", " ", raw).strip()
            return raw
        except Exception:
            return ""
    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    return ""


def _summarize(msg: dict, include_body: bool = False) -> dict:
    headers = msg.get("payload", {}).get("headers", []) or []
    out = {
        "sender": _header(headers, "From"),
        "subject": _header(headers, "Subject") or "(no subject)",
        "date": _format_date(_header(headers, "Date")),
        "read": "UNREAD" not in (msg.get("labelIds") or []),
        "id": msg.get("id", ""),
        "snippet": msg.get("snippet", "") or "",
    }
    if include_body:
        out["content"] = _decode_body(msg.get("payload", {})) or out["snippet"]
    return out


# ---------------------------------------------------------------------------
# Synchronous helpers (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------

def _list_messages_blocking(query: str, max_results: int, include_body: bool = False) -> list[dict]:
    svc = gmail_service()
    if not svc:
        return []
    try:
        resp = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        ids = [m["id"] for m in resp.get("messages", []) or []]
    except Exception as e:
        log.warning(f"messages.list failed (q={query!r}): {e}")
        return []
    out: list[dict] = []
    fmt = "full" if include_body else "metadata"
    for mid in ids:
        try:
            msg = svc.users().messages().get(userId="me", id=mid, format=fmt).execute()
            out.append(_summarize(msg, include_body=include_body))
        except Exception as e:
            log.debug(f"messages.get({mid}) failed: {e}")
    return out


def _unread_count_blocking() -> dict:
    """Return {"total": int, "accounts": {"label/INBOX": count, ...}}.

    Gmail is single-account per OAuth grant, so "accounts" is a single-entry
    dict keyed by the connected email address — preserves the old return shape.
    """
    svc = gmail_service()
    if not svc:
        return {"total": 0, "accounts": {}}
    try:
        label = svc.users().labels().get(userId="me", id="INBOX").execute()
        unread = int(label.get("messagesUnread", 0))
    except Exception as e:
        log.warning(f"labels.get(INBOX) failed: {e}")
        return {"total": 0, "accounts": {}}
    email = get_connected_email() or "Gmail"
    return {"total": unread, "accounts": {email: unread}}


# ---------------------------------------------------------------------------
# Public API — signatures preserved from the prior AppleScript version
# ---------------------------------------------------------------------------

async def get_accounts() -> list[str]:
    email = await asyncio.to_thread(get_connected_email)
    return [email] if email else []


async def get_unread_count() -> dict:
    return await asyncio.to_thread(_unread_count_blocking)


async def get_recent_messages(count: int = 10) -> list[dict]:
    return await asyncio.to_thread(_list_messages_blocking, "in:inbox", count, False)


async def get_unread_messages(count: int = 10) -> list[dict]:
    return await asyncio.to_thread(_list_messages_blocking, "in:inbox is:unread", count, False)


async def get_messages_from_account(account_name: str, count: int = 10) -> list[dict]:
    # Gmail OAuth is per-account; the param is ignored beyond logging. Returned
    # for compatibility with the old API.
    log.debug(f"get_messages_from_account({account_name!r}) — single-account mode")
    return await get_recent_messages(count)


async def search_mail(query: str, count: int = 10) -> list[dict]:
    """Free-form search. Gmail's `q` syntax accepts terms like 'from:foo', 'subject:bar'."""
    return await asyncio.to_thread(_list_messages_blocking, query, count, False)


async def read_message(subject_match: str) -> dict | None:
    """Find the most recent message whose subject matches and return it with body."""
    query = f'subject:"{subject_match}"'
    messages = await asyncio.to_thread(_list_messages_blocking, query, 1, True)
    return messages[0] if messages else None


def format_unread_summary(unread: dict) -> str:
    """Format unread counts for voice."""
    total = unread["total"]
    if total == 0:
        return "Inbox is clear, sir. No unread messages."

    parts = []
    for acct, count in unread["accounts"].items():
        if count > 0:
            parts.append(f"{count} in {acct}")

    if len(parts) == 1:
        return f"You have {total} unread {'message' if total == 1 else 'messages'} — {parts[0]}."
    elif parts:
        return f"You have {total} unread messages: {', '.join(parts)}."
    else:
        return f"You have {total} unread {'message' if total == 1 else 'messages'}."


def format_messages_for_context(messages: list[dict], label: str = "Recent emails") -> str:
    """Format messages as context for the LLM."""
    if not messages:
        return f"{label}: None."
    lines = [f"{label}:"]
    for m in messages[:10]:
        read_marker = "" if m.get("read") else " [UNREAD]"
        line = f"  - {m['sender']}: {m['subject']}{read_marker}"
        if m.get("date"):
            date_str = m["date"]
            if " at " in date_str:
                date_str = date_str.split(" at ")[0].split(", ", 1)[-1] if ", " in date_str else date_str
            line += f" ({date_str})"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Draft creation — the only write surface. No send() function on purpose.
# ---------------------------------------------------------------------------

def _create_draft_blocking(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict | None:
    svc = gmail_service()
    if not svc:
        return None
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    if bcc:
        msg["bcc"] = bcc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        return svc.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()
    except Exception as e:
        log.warning(f"drafts.create failed: {e}")
        return None


async def create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict | None:
    """Create a Gmail draft. Returns the draft object or None on failure.

    The draft lives in your Drafts folder until you click Send yourself —
    VALET never sends mail automatically.
    """
    return await asyncio.to_thread(_create_draft_blocking, to, subject, body, cc, bcc)


def format_messages_for_voice(messages: list[dict]) -> str:
    """Format messages for voice response."""
    if not messages:
        return "No messages to report, sir."

    count = len(messages)
    if count == 1:
        m = messages[0]
        sender = _short_sender(m["sender"])
        return f"One message from {sender}: {m['subject']}."

    summaries = []
    for m in messages[:5]:
        sender = _short_sender(m["sender"])
        summaries.append(f"{sender} regarding {m['subject']}")

    result = f"You have {count} messages. "
    result += ". ".join(summaries[:3])
    if count > 3:
        result += f". And {count - 3} more."
    return result


def _short_sender(sender: str) -> str:
    """Extract just the name from an email sender like 'John Doe <john@example.com>'."""
    if "<" in sender:
        return sender.split("<")[0].strip().strip('"')
    if "@" in sender:
        return sender.split("@")[0]
    return sender
