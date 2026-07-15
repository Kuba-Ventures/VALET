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

# National soccer teams → the FIFA World Cup scoreboard. A question that names
# only the countries ("score of the Argentina England game") carries no league
# keyword, so without this it fell through to slow web research. Mapping nations
# to fifa.world is the right default while a World Cup is on; off-cycle the
# scoreboard window is empty and get_sports degrades to a clean "no fixtures"
# (and the LLM/research fallback still covers non-scores questions). Ordered so
# multi-word names ("united states", "south korea") match before bare tokens.
_NATIONS_WORDS = [
    "united states", "south korea", "saudi arabia", "costa rica", "new zealand",
    "ivory coast", "south africa", "czech republic",
    "argentina", "england", "france", "spain", "brazil", "portugal", "germany",
    "netherlands", "italy", "belgium", "croatia", "morocco", "uruguay",
    "colombia", "mexico", "canada", "japan", "australia", "denmark", "poland",
    "switzerland", "senegal", "ghana", "nigeria", "cameroon", "ecuador", "iran",
    "usa", "korea", "wales", "scotland",
]
_NATIONS: list[tuple[str, str, str, str]] = [
    (w, "soccer", "fifa.world", "FIFA World Cup") for w in _NATIONS_WORDS
]

# Everything the team filter / resolver scans, longest keywords first so a
# multi-word name isn't shadowed by a shorter substring.
_ALL_TEAMS = sorted(_TEAMS + _NATIONS, key=lambda e: -len(e[0]))

# A resolver keyword ("usa") may not appear verbatim in ESPN's event names
# ("United States"), so filtering by the raw keyword found nothing. Map each
# keyword that differs to the substrings that DO appear in event names, plus a
# spoken display name (so "usa" is voiced as "USA", not "Usa"). Keywords not
# listed here match themselves and title-case for display.
_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "usa": ("united states", "usa"),
    "korea": ("korea",),
    "manchester": ("manchester",),  # matches City or United — fine for a hint
}
_TEAM_DISPLAY: dict[str, str] = {
    "usa": "USA",
    "united states": "USA",
}


def _match_terms(hint: str) -> tuple[str, ...]:
    """Substrings that identify `hint` inside an event/team name."""
    return _TEAM_ALIASES.get(hint, (hint,))


def _display_name(hint: str, espn_name: str | None = None) -> str:
    """Prefer ESPN's own team name; else a nice form of the hint (USA, not Usa)."""
    if espn_name:
        return espn_name
    return _TEAM_DISPLAY.get(hint, hint.title())


def _name_matches(hint: str, name: str | None) -> bool:
    low = (name or "").lower()
    return any(term in low for term in _match_terms(hint))


# Team names that read naturally with a leading article ("the United States lost")
# — most nations don't ("Argentina beat England"). Clubs are left bare too
# ("Arsenal beat"), which is idiomatic for soccer.
_TAKES_THE = {
    "united states", "usa", "netherlands", "philippines", "czech republic",
    "ivory coast", "republic of ireland", "gambia",
}


def _team_phrase(name: str) -> str:
    """`name` with a leading article only when it reads naturally."""
    return f"the {name}" if name.lower() in _TAKES_THE else name


# Cup competitions where results span weeks and questions are often historical
# ("who did USA lose to", "how far did England get"). These fetch the whole
# event, not just a few days around today, so past matches are answerable.
_TOURNAMENTS = {"fifa.world", "uefa.champions", "uefa.europa", "uefa.euro"}


def resolve_league(query: str) -> tuple[str, str, str] | None:
    """Map a free-text sports query to (sport_slug, league_slug, display_name).

    Tries explicit league keywords first, then the team/nation map. Returns None
    when nothing matches (caller falls back to web research).
    """
    q = (query or "").lower()
    for kw, sport, league, name in _LEAGUES:
        if kw in q:
            return sport, league, name
    for kw, sport, league, name in _ALL_TEAMS:
        if kw in q:
            return sport, league, name
    return None


def _team_hint(query: str) -> str | None:
    """A lowercase team/nation token to filter events by, if the query names one."""
    q = (query or "").lower()
    for kw, *_ in _ALL_TEAMS:
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


def _range_for(league: str) -> str:
    """ESPN dates window. Domestic leagues: recent results (−3d) through upcoming
    fixtures (+14d) — enough for "did they win" / "when do they play". Cup
    tournaments: reach back ~9 weeks so the whole event (group stage → final) is
    covered, making "who did USA lose to" answerable from the fixtures list."""
    today = datetime.now()
    back = 63 if league in _TOURNAMENTS else 3
    start = (today - timedelta(days=back)).strftime("%Y%m%d")
    end = (today + timedelta(days=14)).strftime("%Y%m%d")
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


async def _fetch_events(sport: str, league: str) -> list[dict] | None:
    """Fetch the games we reason over. ESPN's scoreboard caps a single date-range
    response at ~100 events, so a wide window over a 100+-game tournament silently
    DROPS the most recent games (the semifinals/final happening now). For
    tournaments we therefore fetch TWO smaller windows — recent (−17d..+16d) and
    history (−72d..−17d) — each safely under the cap, and merge them; the union
    has both the current games and the earlier rounds. Non-tournament leagues use
    a single near window."""
    today = datetime.now()

    def win(a: int, b: int) -> str:
        return (f"{(today + timedelta(days=a)).strftime('%Y%m%d')}"
                f"-{(today + timedelta(days=b)).strftime('%Y%m%d')}")

    if league in _TOURNAMENTS:
        recent = await fetch_scoreboard(sport, league, win(-17, 16))
        history = await fetch_scoreboard(sport, league, win(-72, -17))
        if recent is None and history is None:
            return None
        merged: dict[str, dict] = {}
        for e in (history or []) + (recent or []):
            merged[e.get("id")] = e
        out = list(merged.values())
        out.sort(key=lambda e: e.get("date") or "")
        return out

    events = await fetch_scoreboard(sport, league, _range_for(league))
    if events is None:
        return None
    # Some leagues return nothing for a date window but do have a "current"
    # slate — retry the default scoreboard once.
    if not events:
        events = await fetch_scoreboard(sport, league) or []
    return events


async def get_sports(query: str) -> dict | None:
    """Top-level lookup used by the server. Resolves the league, fetches the
    games window(s), optionally filters to a named team, and returns a dict with
    a card `payload` and a spoken `summary`. None → let the caller fall back to
    web research."""
    resolved = resolve_league(query)
    if not resolved:
        return None
    sport, league, display = resolved

    events = await _fetch_events(sport, league)
    if events is None:
        return None

    hint = _team_hint(query)
    if hint:
        def _has(e: dict) -> bool:
            return (
                _name_matches(hint, (e.get("home") or {}).get("name"))
                or _name_matches(hint, (e.get("away") or {}).get("name"))
                or _name_matches(hint, e.get("short_name"))
            )
        filtered = [e for e in events if _has(e)]
        # The user named a team: if it has no games in the window, say so —
        # never fall back to speaking an unrelated game from the same league.
        if not filtered:
            empty = {"live": [], "recent": [], "upcoming": []}
            return {
                "payload": build_card_payload(display, empty),
                "summary": f"I found no recent or upcoming {_display_name(hint)} fixtures, sir.",
            }
        events = filtered

    buckets = _bucket(events)
    return {
        "payload": build_card_payload(display, buckets),
        "summary": format_voice_summary(display, buckets, query, hint),
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


def _recent_line(display: str, buckets: dict[str, list[dict]]) -> str | None:
    if buckets["recent"]:
        e = buckets["recent"][-1]
        return f"{display}, sir. Final: {_score_line(e)}."
    return None


def _sides_for(g: dict, hint: str) -> tuple[dict | None, dict | None]:
    """Return (team_side, opponent_side) for the hinted team in a game."""
    for a, b in (("home", "away"), ("away", "home")):
        s, o = g.get(a) or {}, g.get(b) or {}
        if _name_matches(hint, s.get("name")):
            return s, o
    return None, None


def _team_result_line(display: str, buckets: dict[str, list[dict]], hint: str, q: str) -> str | None:
    """Team-centric result phrasing ("The USA lost to Belgium, 4 to 1").
    Honors "lose"/"beat" intent by picking the most recent loss/win; otherwise
    the team's most recent completed game."""
    posts = buckets["recent"]
    if not posts:
        return None
    # "who did X lose to" → most recent loss; "who did X beat/defeat" → most
    # recent win. NOTE: bare "win"/"won" ("did they win?") is NOT a win-hunt —
    # it's a yes/no about the last game, so it falls through to "most recent".
    want_loss = any(w in q for w in ("lose", "lost", "knocked out", "eliminat"))
    want_win = (not want_loss) and any(w in q for w in ("beat", "defeat"))

    def lost(g: dict) -> bool:
        _, o = _sides_for(g, hint)
        return bool(o and o.get("winner"))

    def won(g: dict) -> bool:
        s, _ = _sides_for(g, hint)
        return bool(s and s.get("winner"))

    cand = posts
    if want_loss:
        cand = [g for g in posts if lost(g)] or posts
    elif want_win:
        cand = [g for g in posts if won(g)] or posts

    g = cand[-1]
    s, o = _sides_for(g, hint)
    if not (s and o):
        return f"{display}, sir. Final: {_score_line(g)}."
    subj = _team_phrase(_display_name(hint, s.get("name")))
    oname = o.get("name", "their opponent")
    ts, os_ = s.get("score"), o.get("score")
    if o.get("winner"):
        line = f"{subj} lost to {oname}, {os_} to {ts}, sir."
    elif s.get("winner"):
        line = f"{subj} beat {oname}, {ts} to {os_}, sir."
    else:
        line = f"{subj} drew with {oname}, {ts} to {os_}, sir."
    return line[0].upper() + line[1:]


def _upcoming_line(display: str, buckets: dict[str, list[dict]]) -> str | None:
    if buckets["upcoming"]:
        parts = [
            f"{_score_line(e)} — {_fmt_when(e.get('date'), e.get('detail_full'))}"
            for e in buckets["upcoming"][:2]
        ]
        lead = "Next up" if len(parts) == 1 else "Upcoming fixtures"
        return f"{display}, sir. {lead}: " + "; ".join(parts) + "."
    return None


def format_voice_summary(
    display: str, buckets: dict[str, list[dict]], query: str = "", hint: str | None = None
) -> str:
    """Concise butler-style spoken line, prioritized by what the user asked:

    - Live game in progress → always leads (most timely).
    - A RESULT question about a specific team ("who did USA lose to", "did they
      win") → team-centric result ("The USA lost to Belgium, 4 to 1").
    - A RESULT question with no team → the most recent final.
    - A SCHEDULE question ("when", "what time", "next", "upcoming") → the next
      fixtures.
    - Otherwise → next fixture if any, else the last result.
    """
    q = (query or "").lower()
    if buckets["live"]:
        e = buckets["live"][0]
        return f"{display}, sir: {_score_line(e)}, {e.get('detail') or 'in progress'}."

    result_intent = any(
        w in q for w in (
            "score", "did ", "won", " win", "beat", "result", "final",
            "how did", "was the", "lose", "lost", "outcome", "knocked out",
        )
    )
    schedule_intent = any(
        w in q for w in (
            "when", "what time", "next", "upcoming", "schedule", "fixture",
            "play", "kick", "start",
        )
    )

    if result_intent and not schedule_intent:
        if hint:
            line = _team_result_line(display, buckets, hint, q)
            if line:
                return line
        order = [_recent_line, _upcoming_line]
    else:
        order = [_upcoming_line, _recent_line]
    for fn in order:
        line = fn(display, buckets)
        if line:
            return line
    return f"I found no current {display} fixtures, sir."
