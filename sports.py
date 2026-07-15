"""Live sports scores, schedules, and fixtures via ESPN's public site API.

Keyless, like the Open-Meteo weather source — ESPN's `site.api.espn.com`
scoreboard endpoints need no API key. Mirrors the shape of `weather.py`:
async httpx calls, every external failure caught and turned into `None`, and a
`build_card_payload()` the frontend renders plus a `format_voice_summary()` the
butler speaks.

NOTE: this is ESPN's UNOFFICIAL/undocumented API. Field names here reflect the
observed response shape (verified against the 2026 FIFA World Cup and major US
leagues); ESPN can change it without notice, so every access is defensive and
the server falls back to web RESEARCH when resolution or fetch returns None.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import httpx

log = logging.getLogger("valet.sports")

_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_TIMEOUT = 12.0

# ---------------------------------------------------------------------------
# League resolution — map natural-language keywords to ESPN (sport, league)
# slugs. Longer / more specific phrases MUST come first so "college football"
# wins over "football" and "champions league" over "league". Each entry is
# (keyword, sport_slug, league_slug, display_name).
# ---------------------------------------------------------------------------
_LEAGUES: list[tuple[str, str, str, str]] = [
    # Soccer — multi-word first
    ("fifa world cup", "soccer", "fifa.world", "FIFA World Cup"),
    ("world cup", "soccer", "fifa.world", "FIFA World Cup"),
    ("champions league", "soccer", "uefa.champions", "UEFA Champions League"),
    ("europa league", "soccer", "uefa.europa", "UEFA Europa League"),
    ("premier league", "soccer", "eng.1", "Premier League"),
    ("english premier", "soccer", "eng.1", "Premier League"),
    ("la liga", "soccer", "esp.1", "La Liga"),
    ("serie a", "soccer", "ita.1", "Serie A"),
    ("bundesliga", "soccer", "ger.1", "Bundesliga"),
    ("ligue 1", "soccer", "fra.1", "Ligue 1"),
    ("mls", "soccer", "usa.1", "MLS"),
    ("epl", "soccer", "eng.1", "Premier League"),
    ("ucl", "soccer", "uefa.champions", "UEFA Champions League"),
    # US leagues
    ("college football", "football", "college-football", "College Football"),
    ("cfb", "football", "college-football", "College Football"),
    ("ncaaf", "football", "college-football", "College Football"),
    ("college basketball", "basketball", "mens-college-basketball", "College Basketball"),
    ("ncaab", "basketball", "mens-college-basketball", "College Basketball"),
    ("nfl", "football", "nfl", "NFL"),
    ("nba", "basketball", "nba", "NBA"),
    ("wnba", "basketball", "wnba", "WNBA"),
    ("mlb", "baseball", "mlb", "MLB"),
    ("nhl", "hockey", "nhl", "NHL"),
]

# A small set of well-known teams → league, so a bare "did the Lakers win"
# resolves without the user naming the league. Deliberately short (popular
# teams only); anything unmatched falls back to web research.
_TEAMS: list[tuple[str, str, str, str]] = [
    ("lakers", "basketball", "nba", "NBA"),
    ("celtics", "basketball", "nba", "NBA"),
    ("warriors", "basketball", "nba", "NBA"),
    ("knicks", "basketball", "nba", "NBA"),
    ("cowboys", "football", "nfl", "NFL"),
    ("49ers", "football", "nfl", "NFL"),
    ("chiefs", "football", "nfl", "NFL"),
    ("eagles", "football", "nfl", "NFL"),
    ("yankees", "baseball", "mlb", "MLB"),
    ("dodgers", "baseball", "mlb", "MLB"),
    ("red sox", "baseball", "mlb", "MLB"),
    ("arsenal", "soccer", "eng.1", "Premier League"),
    ("liverpool", "soccer", "eng.1", "Premier League"),
    ("manchester", "soccer", "eng.1", "Premier League"),
    ("real madrid", "soccer", "esp.1", "La Liga"),
    ("barcelona", "soccer", "esp.1", "La Liga"),
]


def resolve_league(query: str) -> tuple[str, str, str] | None:
    """Map a free-text sports query to (sport_slug, league_slug, display_name).

    Tries explicit league keywords first, then a small popular-team map.
    Returns None when nothing matches (caller falls back to web research).
    """
    q = (query or "").lower()
    for kw, sport, league, name in _LEAGUES:
        if kw in q:
            return sport, league, name
    for kw, sport, league, name in _TEAMS:
        if kw in q:
            return sport, league, name
    return None


def _team_hint(query: str) -> str | None:
    """A lowercase team token to filter events by, if the query names one."""
    q = (query or "").lower()
    for kw, *_ in _TEAMS:
        if kw in q:
            return kw
    return None


def _normalize_event(ev: dict) -> dict | None:
    """Flatten one ESPN scoreboard event into the fields the UI/voice need."""
    try:
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        home = away = None
        for c in competitors:
            side = {
                "name": (c.get("team") or {}).get("displayName")
                or (c.get("team") or {}).get("name")
                or "TBD",
                "abbrev": (c.get("team") or {}).get("abbreviation") or "",
                "score": c.get("score"),
                "logo": (c.get("team") or {}).get("logo"),
                "winner": c.get("winner"),
            }
            if c.get("homeAway") == "home":
                home = side
            else:
                away = side
        status = (ev.get("status") or {}).get("type") or {}
        return {
            "id": ev.get("id"),
            "name": ev.get("name"),
            "short_name": ev.get("shortName"),
            "date": ev.get("date"),  # ISO Zulu, e.g. 2026-07-18T21:00Z
            "state": status.get("state"),  # pre | in | post
            # `shortDetail` is concise for live/final ("FT", "2nd Quarter");
            # `detail` carries the full local kickoff for upcoming games
            # ("Sat, July 18th at 5:00 PM EDT") — keep both, pick per state.
            "detail": status.get("shortDetail") or status.get("detail"),
            "detail_full": status.get("detail"),
            "completed": bool(status.get("completed")),
            "home": home,
            "away": away,
            "venue": ((comp.get("venue") or {}).get("fullName")),
        }
    except Exception as e:  # defensive: never let one malformed event break all
        log.debug("normalize_event failed: %s", e)
        return None


async def fetch_scoreboard(
    sport: str, league: str, date_range: str | None = None
) -> list[dict] | None:
    """Fetch and normalize a league scoreboard. `date_range` is an ESPN dates
    param — "YYYYMMDD" or "YYYYMMDD-YYYYMMDD". None → ESPN's current slate.

    Returns a list of normalized events (possibly empty) or None on failure.
    """
    url = f"{_BASE}/{sport}/{league}/scoreboard"
    params = {"dates": date_range} if date_range else {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("ESPN fetch failed (%s/%s): %s", sport, league, e)
        return None
    events = data.get("events") or []
    out = [ne for ev in events if (ne := _normalize_event(ev))]
    out.sort(key=lambda e: e.get("date") or "")
    return out


def _default_range() -> str:
    """A window around today: recent results (−3d) through upcoming fixtures
    (+10d). One call covers both "what was the score" and "when do they play"."""
    today = datetime.now()
    start = (today - timedelta(days=3)).strftime("%Y%m%d")
    end = (today + timedelta(days=10)).strftime("%Y%m%d")
    return f"{start}-{end}"


def _bucket(events: list[dict]) -> dict[str, list[dict]]:
    """Split events into live / recent-final / upcoming, each capped for a
    focused card + summary (busy leagues can return dozens of games)."""
    live = [e for e in events if e.get("state") == "in"]
    finals = [e for e in events if e.get("state") == "post"]
    upcoming = [e for e in events if e.get("state") == "pre"]
    # Most-recent finals last in the sorted list; take the tail.
    return {
        "live": live[:6],
        "recent": finals[-4:],
        "upcoming": upcoming[:6],
    }


async def get_sports(query: str) -> dict | None:
    """Top-level lookup used by the server. Resolves the league, fetches a
    window of games, optionally filters to a named team, and returns a dict
    with a card `payload` and a spoken `summary`. None → let the caller fall
    back to web research."""
    resolved = resolve_league(query)
    if not resolved:
        return None
    sport, league, display = resolved

    events = await fetch_scoreboard(sport, league, _default_range())
    if events is None:
        return None
    # Some leagues (e.g. mid-tournament) return nothing for a date window but
    # do have a "current" slate — retry the default scoreboard once.
    if not events:
        events = await fetch_scoreboard(sport, league) or []

    hint = _team_hint(query)
    if hint:
        def _has(e: dict) -> bool:
            blob = " ".join(
                filter(None, [
                    (e.get("home") or {}).get("name", ""),
                    (e.get("away") or {}).get("name", ""),
                    e.get("short_name") or "",
                ])
            ).lower()
            return hint in blob
        filtered = [e for e in events if _has(e)]
        # The user named a team: if it has no games in the window, say so —
        # never fall back to speaking an unrelated game from the same league.
        if not filtered:
            empty = {"live": [], "recent": [], "upcoming": []}
            team = hint.title()
            return {
                "payload": build_card_payload(display, empty),
                "summary": f"I found no recent or upcoming {team} fixtures, sir.",
            }
        events = filtered

    buckets = _bucket(events)
    return {
        "payload": build_card_payload(display, buckets),
        "summary": format_voice_summary(display, buckets),
    }


def _score_line(e: dict) -> str:
    """"Argentina 2, England 1" or "England vs France"."""
    home = e.get("home") or {}
    away = e.get("away") or {}
    hn, an = home.get("name", "TBD"), away.get("name", "TBD")
    if e.get("state") == "pre":
        return f"{an} vs {hn}"
    hs, as_ = home.get("score"), away.get("score")
    if hs is not None and as_ is not None:
        return f"{an} {as_}, {hn} {hs}"
    return f"{an} vs {hn}"


def build_card_payload(display: str, buckets: dict[str, list[dict]]) -> dict:
    """Frontend-ready dict for the `result.sports` card."""
    def cell(e: dict) -> dict:
        # Upcoming games show the full local kickoff; live/final show the
        # concise status ("FT", "2nd Quarter", "78'").
        when = _fmt_when(e.get("date"), e.get("detail_full")) if e.get("state") == "pre" else e.get("detail")
        return {
            "matchup": _score_line(e),
            "state": e.get("state"),
            "detail": when,
            "date": e.get("date"),
            "venue": e.get("venue"),
            "home": e.get("home"),
            "away": e.get("away"),
        }
    return {
        "league": display,
        "live": [cell(e) for e in buckets["live"]],
        "recent": [cell(e) for e in buckets["recent"]],
        "upcoming": [cell(e) for e in buckets["upcoming"]],
        "updated_at": datetime.now().strftime("%-I:%M %p"),
    }


def _fmt_when(iso: str | None, detail_full: str | None) -> str:
    """Prefer ESPN's human detail ("Sat, July 18th at 5:00 PM EDT") — it's
    already in a sensible local zone. Otherwise parse the UTC ISO and convert
    to the machine's LOCAL timezone (never print raw UTC — that's how the
    third-place match read as 9 PM instead of 5 PM EDT)."""
    if detail_full and re.search(r"\d", detail_full):
        return detail_full
    if not iso:
        return "TBD"
    try:
        dt = datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M%z")
        return dt.astimezone().strftime("%A %B %-d at %-I:%M %p")
    except Exception:
        return iso


def format_voice_summary(display: str, buckets: dict[str, list[dict]]) -> str:
    """Concise butler-style spoken line: live > next upcoming > most recent."""
    if buckets["live"]:
        e = buckets["live"][0]
        return f"{display}, sir: {_score_line(e)}, {e.get('detail') or 'in progress'}."
    if buckets["upcoming"]:
        parts = []
        for e in buckets["upcoming"][:2]:
            parts.append(f"{_score_line(e)} — {_fmt_when(e.get('date'), e.get('detail_full'))}")
        lead = "Next up" if len(parts) == 1 else "Upcoming fixtures"
        return f"{display}, sir. {lead}: " + "; ".join(parts) + "."
    if buckets["recent"]:
        e = buckets["recent"][-1]
        return f"{display}, sir. Final: {_score_line(e)}."
    return f"I found no current {display} fixtures, sir."
