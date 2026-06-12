"""Google Calendar reads via the Google Calendar API.

Returns events shaped EXACTLY like apple_calendar.read_events so the two sources
can be merged and de-duplicated by server.py. Returns [] when Google isn't
connected or on any error — never raises. Read-only here; creates still go
through Apple Calendar (which writes back to the synced Google account).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import google_auth

log = logging.getLogger("valet.gcal")


def _local_day_bounds(date_str: str | None):
    """(start, end) timezone-aware datetimes spanning the local day."""
    local_tz = datetime.now().astimezone().tzinfo
    if date_str:
        y, m, d = (int(x) for x in date_str.split("-"))
        start = datetime(y, m, d, tzinfo=local_tz)
    else:
        now = datetime.now(local_tz)
        start = datetime(now.year, now.month, now.day, tzinfo=local_tz)
    return start, start + timedelta(days=1)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _shape(ev: dict) -> dict | None:
    """Map a Google event to apple_calendar's dict shape (start_iso is UTC, so
    the two sources de-dup cleanly on a normalized minute/day key)."""
    start = ev.get("start", {}) or {}
    all_day = "date" in start and "dateTime" not in start
    if all_day:
        sdt = _parse_iso(start.get("date", ""))
        if sdt is None:
            return None
        start_iso = sdt.replace(tzinfo=timezone.utc).isoformat()
        time_str = ""
    else:
        sdt = _parse_iso(start.get("dateTime", ""))
        if sdt is None:
            return None
        start_iso = sdt.astimezone(timezone.utc).isoformat()
        time_str = sdt.astimezone().strftime("%I:%M %p").lstrip("0")
    return {
        "title": str(ev.get("summary") or "Untitled"),
        "start_iso": start_iso,
        "time_str": time_str,
        "all_day": all_day,
        "location": str(ev.get("location") or ""),
    }


def _read_blocking(date_str: str | None) -> list[dict]:
    svc = google_auth.calendar_service()
    if svc is None:
        return []
    start, end = _local_day_bounds(date_str)
    time_min = start.astimezone(timezone.utc).isoformat()
    time_max = end.astimezone(timezone.utc).isoformat()
    out: list[dict] = []
    try:
        # Read every selected calendar (not just primary) so secondary Google
        # calendars are covered. Falls back to "primary" if the list call fails.
        cal_ids = ["primary"]
        try:
            cal_list = svc.calendarList().list(maxResults=50).execute()
            ids = [c["id"] for c in cal_list.get("items", []) if c.get("selected", True)]
            cal_ids = ids or ["primary"]
        except Exception as e:
            log.warning(f"calendarList failed, using primary: {e}")
        for cid in cal_ids:
            resp = svc.events().list(
                calendarId=cid, timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy="startTime", maxResults=50,
            ).execute()
            for ev in resp.get("items", []):
                shaped = _shape(ev)
                if shaped:
                    out.append(shaped)
    except Exception as e:
        log.warning(f"google read_events failed: {e}")
        return []
    out.sort(key=lambda x: x["start_iso"])
    return out


async def read_events(date_str: str | None = None) -> list[dict]:
    """Today's (or a YYYY-MM-DD) Google Calendar events. [] if not connected."""
    if not google_auth.is_connected():
        return []
    return await asyncio.to_thread(_read_blocking, date_str)
