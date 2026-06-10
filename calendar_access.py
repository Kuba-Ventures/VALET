"""Google Calendar access — read + write.

Replaces the prior AppleScript-based Apple Calendar integration. Public
function signatures and return shapes are preserved so server.py callers
don't need to change. Events are returned as dicts with keys:

    title (str), start (str like "9:00 AM"), all_day (bool), calendar (str)

A short in-memory cache reduces API calls during quick successive lookups.

Write functions: create_event(), delete_event(), update_event(). Require the
calendar OAuth scope (not just calendar.readonly) — see google_auth.SCOPES.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from google_auth import calendar_service

log = logging.getLogger("valet.calendar")

# Cache so back-to-back queries don't hammer the API.
_cache: dict[str, list[dict]] = {"today": [], "upcoming": []}
_cache_ts: float = 0.0
_CACHE_TTL = 60.0  # seconds


def _local_tz() -> ZoneInfo:
    """Resolve the user's local timezone. Falls back to America/New_York."""
    name = os.environ.get("TZ")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    try:
        # macOS exposes the configured zone via /etc/localtime symlink
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return ZoneInfo(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    return ZoneInfo("America/New_York")


def _format_event_time(iso: str | None, all_day: bool) -> str:
    if all_day or not iso:
        return ""
    # Google returns RFC3339 timestamps; for date-only (all-day) it returns just YYYY-MM-DD.
    try:
        if "T" in iso:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            local = dt.astimezone(_local_tz())
            return local.strftime("%-I:%M %p").lstrip("0")
        return iso  # fallback
    except Exception:
        return iso


def _calendar_filter() -> set[str] | None:
    """Optional whitelist of calendar account emails from CALENDAR_ACCOUNTS env var.

    Returns None when unset (= include all calendars), else a set of lower-cased
    email addresses. Maintained for parity with the old Apple-Calendar behavior.
    """
    raw = os.getenv("CALENDAR_ACCOUNTS", "").strip()
    if not raw or raw.lower() == "auto":
        return None
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _is_all_day(start_obj: dict) -> bool:
    return "date" in start_obj and "dateTime" not in start_obj


def _parse_event(g_event: dict, calendar_name: str) -> dict | None:
    summary = (g_event.get("summary") or "").strip() or "(no title)"
    start_obj = g_event.get("start") or {}
    end_obj = g_event.get("end") or {}
    all_day = _is_all_day(start_obj)

    iso = start_obj.get("dateTime") or start_obj.get("date")
    if not iso:
        return None

    return {
        "title": summary,
        "start": _format_event_time(iso, all_day),
        "all_day": all_day,
        "calendar": calendar_name,
        # Extra context — not used by the existing formatters but useful for
        # callers that want raw timestamps. Safe to add since dict.get() ignores.
        "start_iso": iso,
        "end_iso": end_obj.get("dateTime") or end_obj.get("date") or "",
    }


def _list_events_blocking(time_min: datetime, time_max: datetime) -> list[dict]:
    """Synchronous core: query CalendarList, then events on each filtered calendar."""
    svc = calendar_service()
    if not svc:
        return []

    tz_min = time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    tz_max = time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    cal_filter = _calendar_filter()

    out: list[dict] = []
    try:
        cals = svc.calendarList().list(maxResults=50).execute().get("items", [])
    except Exception as e:
        log.warning(f"calendarList failed: {e}")
        return []

    for cal in cals:
        cal_id = cal.get("id", "")
        cal_name = cal.get("summary") or cal_id
        if cal_filter and cal_id.lower() not in cal_filter and cal_name.lower() not in cal_filter:
            continue
        try:
            resp = svc.events().list(
                calendarId=cal_id,
                timeMin=tz_min,
                timeMax=tz_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
        except Exception as e:
            log.debug(f"events.list failed for {cal_name}: {e}")
            continue
        for item in resp.get("items", []):
            parsed = _parse_event(item, cal_name)
            if parsed:
                out.append(parsed)

    # Sort by start, putting all-day first.
    out.sort(key=lambda e: (not e["all_day"], e.get("start_iso", "")))
    return out


async def _list_events(time_min: datetime, time_max: datetime) -> list[dict]:
    return await asyncio.to_thread(_list_events_blocking, time_min, time_max)


# ---------------------------------------------------------------------------
# Public API — function signatures preserved from the prior AppleScript version
# ---------------------------------------------------------------------------

async def refresh_cache() -> None:
    """Refresh today's + next-24-hours event caches."""
    global _cache, _cache_ts
    tz = _local_tz()
    now = datetime.now(tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    today = await _list_events(today_start, today_end)
    upcoming = await _list_events(now, now + timedelta(hours=24))
    _cache = {"today": today, "upcoming": upcoming}
    _cache_ts = asyncio.get_event_loop().time()


def _cache_fresh() -> bool:
    loop = asyncio.get_event_loop()
    return _cache_ts > 0 and (loop.time() - _cache_ts) < _CACHE_TTL


async def get_todays_events() -> list[dict]:
    if not _cache_fresh():
        await refresh_cache()
    return list(_cache["today"])


async def get_upcoming_events(hours: int = 4) -> list[dict]:
    tz = _local_tz()
    now = datetime.now(tz)
    horizon = now + timedelta(hours=hours)
    # Reuse the 24h cache when the horizon fits inside it.
    if _cache_fresh() and hours <= 24:
        upcoming = _cache["upcoming"]
    else:
        upcoming = await _list_events(now, horizon)
    return [e for e in upcoming if not e.get("start_iso", "").endswith("00:00:00") or e["all_day"] or True][:50]


async def get_next_event() -> dict | None:
    events = await get_upcoming_events(hours=48)
    return events[0] if events else None


async def get_events_for_date(date_str: str) -> list[dict]:
    """Fetch events for a specific calendar date (YYYY-MM-DD or natural ISO).

    Used when the user asks about a non-today date ("what's on May 21?",
    "show me Thursday's events"). The LLM resolves natural-language dates
    to YYYY-MM-DD using the CURRENT TIME context before calling.
    """
    tz = _local_tz()
    # Tolerate a few input shapes — strip time component if present.
    raw = date_str.strip()
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    if " " in raw:
        raw = raw.split(" ", 1)[0]
    try:
        d = datetime.fromisoformat(raw).replace(tzinfo=tz)
    except ValueError:
        raise ValueError(f"Couldn't parse date: {date_str!r}")
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return await _list_events(start, end)


# ---------------------------------------------------------------------------
# Write operations — create / update / delete events on the primary calendar
# ---------------------------------------------------------------------------

def _parse_user_datetime(s: str) -> datetime:
    """Parse a flexible user-supplied date/time string into a tz-aware datetime.

    Accepts ISO 8601 ("2026-05-15T15:00:00") or a few common natural variants
    ("2026-05-15 3pm", "tomorrow 3pm" is NOT handled here — the LLM resolves
    those into absolute times before invoking the action). All returned
    datetimes are in the user's local timezone.
    """
    tz = _local_tz()
    s = s.strip()
    # Try ISO first.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt
    except ValueError:
        pass
    # Try "YYYY-MM-DD H[am/pm]" or "YYYY-MM-DD HH:MM".
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %I%p", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M%p"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError(f"Couldn't parse datetime: {s!r}")


def _create_event_blocking(title: str, start: datetime, end: datetime,
                           description: str | None, location: str | None) -> dict | None:
    svc = calendar_service()
    if not svc:
        return None
    body = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": str(start.tzinfo)},
        "end":   {"dateTime": end.isoformat(),   "timeZone": str(end.tzinfo)},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    try:
        return svc.events().insert(calendarId="primary", body=body).execute()
    except Exception as e:
        log.warning(f"events.insert failed: {e}")
        return None


async def create_event(title: str, start_str: str, end_str: str | None = None,
                       duration_minutes: int = 30,
                       description: str | None = None,
                       location: str | None = None) -> dict | None:
    """Create a calendar event on the primary calendar.

    start_str / end_str: ISO 8601 or "YYYY-MM-DD H:MM AM/PM" format.
    If end_str is omitted, end = start + duration_minutes.
    """
    start = _parse_user_datetime(start_str)
    end = _parse_user_datetime(end_str) if end_str else (start + timedelta(minutes=duration_minutes))
    event = await asyncio.to_thread(_create_event_blocking, title, start, end, description, location)
    if event:
        # Bust the cache so the new event shows in the next system prompt.
        global _cache_ts
        _cache_ts = 0.0
    return event


def _find_event_blocking(query: str, on_date: datetime | None = None) -> dict | None:
    """Find a single event matching a fuzzy title search, optionally on a date."""
    svc = calendar_service()
    if not svc:
        return None
    tz = _local_tz()
    if on_date:
        time_min = on_date.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=1)
    else:
        # Default: a 14-day window centered on today.
        now = datetime.now(tz)
        time_min = now - timedelta(days=1)
        time_max = now + timedelta(days=14)

    try:
        resp = svc.events().list(
            calendarId="primary",
            timeMin=time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            timeMax=time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            q=query,
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute()
    except Exception as e:
        log.warning(f"events.list (find) failed: {e}")
        return None
    items = resp.get("items", [])
    return items[0] if items else None


async def delete_event(query: str, on_date_str: str | None = None) -> dict:
    """Cancel an event by fuzzy title match. Returns {success, confirmation}."""
    on_date = _parse_user_datetime(on_date_str) if on_date_str else None
    event = await asyncio.to_thread(_find_event_blocking, query, on_date)
    if not event:
        return {"success": False, "confirmation": f"I couldn't find an event matching '{query}', sir."}

    svc = calendar_service()
    if not svc:
        return {"success": False, "confirmation": "Calendar isn't connected, sir."}

    try:
        await asyncio.to_thread(
            lambda: svc.events().delete(calendarId="primary", eventId=event["id"]).execute()
        )
    except Exception as e:
        log.warning(f"events.delete failed: {e}")
        return {"success": False, "confirmation": f"Couldn't cancel the event, sir: {e}"}

    global _cache_ts
    _cache_ts = 0.0
    return {
        "success": True,
        "confirmation": f"Cancelled '{event.get('summary', '(no title)')}', sir.",
        "deleted_event": event,
    }


async def get_calendar_names() -> list[str]:
    svc = calendar_service()
    if not svc:
        return []
    try:
        cals = await asyncio.to_thread(
            lambda: svc.calendarList().list(maxResults=50).execute().get("items", [])
        )
    except Exception as e:
        log.warning(f"calendarList failed: {e}")
        return []
    return [c.get("summary") or c.get("id", "") for c in cals]


def format_events_for_context(events: list[dict]) -> str:
    """Format events as context for the LLM."""
    if not events:
        return "No events scheduled today."
    lines = []
    for evt in events:
        if evt.get("all_day"):
            entry = f"  All day — {evt['title']}"
        else:
            entry = f"  {evt['start']} — {evt['title']}"
        if evt.get("calendar"):
            entry += f" [{evt['calendar']}]"
        lines.append(entry)
    return "\n".join(lines)


def format_schedule_summary(events: list[dict]) -> str:
    """Format a brief voice-friendly summary of the schedule."""
    if not events:
        return "Your schedule is clear today, sir."

    count = len(events)
    if count == 1:
        evt = events[0]
        if evt.get("all_day"):
            return f"You have one all-day event: {evt['title']}."
        return f"You have one event: {evt['title']} at {evt['start']}."

    summaries = []
    for evt in events[:5]:
        if evt.get("all_day"):
            summaries.append(f"{evt['title']} all day")
        else:
            summaries.append(f"{evt['title']} at {evt['start']}")

    result = f"You have {count} events today. "
    result += ". ".join(summaries[:3])
    if count > 3:
        result += f". And {count - 3} more."
    return result
