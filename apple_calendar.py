"""Apple Calendar via EventKit — fast local read + create. Uses the Calendars
permission (a separate TCC grant from Automation); no Google account needed.

Authorization (macOS 14+): FULL access (status 3) is required to read AND create.
Write-only (4) can create but not read. We request full access. All functions
are defensive — a missing framework or denied permission returns empty / an
error dict, never raises.
"""

import logging
import threading
from datetime import datetime

log = logging.getLogger("valet.apple_calendar")

# EKAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied,
# 3 fullAccess (macOS 14+), 4 writeOnly (macOS 14+).
_store = None


def _store_obj():
    global _store
    if _store is None:
        from EventKit import EKEventStore
        _store = EKEventStore.alloc().init()
    return _store


def auth_status() -> int:
    """Current calendar authorization status, or -1 if EventKit is unavailable."""
    try:
        from EventKit import EKEventStore, EKEntityTypeEvent
        return int(EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent))
    except Exception as e:
        log.warning(f"calendar auth_status failed: {e}")
        return -1


def has_read_access() -> bool:
    """Read needs full access (3). Write-only (4) cannot read."""
    return auth_status() == 3


def request_access(timeout: float = 60) -> bool:
    """Request FULL calendar access — triggers the native prompt the first time.
    Returns True once full access is granted. Blocks up to `timeout` for the
    user's response (the completion handler fires on EventKit's own queue)."""
    try:
        store = _store_obj()
        result = {"granted": False}
        done = threading.Event()

        def cb(granted, err):
            result["granted"] = bool(granted)
            done.set()

        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            store.requestFullAccessToEventsWithCompletion_(cb)
        else:  # pre-macOS-14 fallback
            from EventKit import EKEntityTypeEvent
            store.requestAccessToEntityType_completion_(EKEntityTypeEvent, cb)
        done.wait(timeout=timeout)
        # Confirm via the status, not just the callback bool (write-only returns
        # granted=True for a write request but can't read).
        return has_read_access() or result["granted"]
    except Exception as e:
        log.warning(f"calendar access request failed: {e}")
        return False


def _day_bounds(date_str: str | None):
    """(startOfDay, startOfNextDay) NSDates for today or a YYYY-MM-DD string."""
    from Foundation import NSDate, NSCalendar, NSDateComponents
    cal = NSCalendar.currentCalendar()
    if date_str:
        y, m, d = (int(x) for x in date_str.split("-"))
        comps = NSDateComponents.alloc().init()
        comps.setYear_(y)
        comps.setMonth_(m)
        comps.setDay_(d)
        base = cal.dateFromComponents_(comps)
    else:
        base = NSDate.date()
    start = cal.startOfDayForDate_(base)
    one = NSDateComponents.alloc().init()
    one.setDay_(1)
    end = cal.dateByAddingComponents_toDate_options_(one, start, 0)
    return start, end


def read_events(date_str: str | None = None) -> list[dict]:
    """Events for today (or a YYYY-MM-DD date). Empty list without read access."""
    if not has_read_access():
        return []
    try:
        from Foundation import NSDateFormatter
        store = _store_obj()
        start, end = _day_bounds(date_str)
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(start, end, None)
        fmt = NSDateFormatter.alloc().init()
        fmt.setDateFormat_("h:mm a")  # local timezone by default
        out = []
        for e in (store.eventsMatchingPredicate_(pred) or []):
            sd = e.startDate()
            out.append({
                "title": str(e.title() or "Untitled"),
                "start_iso": sd.description() if sd else "",      # UTC, for sorting
                "time_str": "" if e.isAllDay() or not sd else str(fmt.stringFromDate_(sd)),
                "all_day": bool(e.isAllDay()),
                "location": str(e.location() or ""),
            })
        out.sort(key=lambda x: x["start_iso"])
        return out
    except Exception as e:
        log.warning(f"read_events failed: {e}")
        return []


def _parse_dt(s: str) -> datetime | None:
    """Parse the time strings VALET emits: ISO, or 'YYYY-MM-DD H:MM AM/PM', or
    'YYYY-MM-DD HH:MM', or a bare date. Returns a naive local datetime."""
    s = (s or "").strip()
    if not s:
        return None
    iso = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %I %p", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def create_event(
    title: str,
    start_str: str,
    end_str: str | None = None,
    duration_minutes: int = 30,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Create an event in the default calendar. {success: bool, error?: str}.
    Mirrors calendar_access.create_event's signature so it's a drop-in."""
    try:
        from EventKit import EKEvent, EKSpanThisEvent
        from Foundation import NSDate
        start_dt = _parse_dt(start_str)
        if start_dt is None:
            return {"success": False, "error": f"couldn't parse start time '{start_str}'"}
        end_dt = _parse_dt(end_str) if end_str else None
        store = _store_obj()
        ev = EKEvent.eventWithEventStore_(store)
        ev.setTitle_(title or "Event")
        start = NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp())
        ev.setStartDate_(start)
        end = (NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp()) if end_dt
               else NSDate.dateWithTimeInterval_sinceDate_(max(1, duration_minutes) * 60, start))
        ev.setEndDate_(end)
        if location:
            ev.setLocation_(location)
        if description:
            ev.setNotes_(description)
        ev.setCalendar_(store.defaultCalendarForNewEvents())
        ok, err = store.saveEvent_span_error_(ev, EKSpanThisEvent, None)
        if not ok:
            return {"success": False, "error": str(err)}
        return {"success": True, "title": title}
    except Exception as e:
        log.warning(f"create_event failed: {e}")
        return {"success": False, "error": str(e)}
