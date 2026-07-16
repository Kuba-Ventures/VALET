"""Universal live-sports answers via ESPN's public (undocumented) APIs.

Keyless, like the Open-Meteo weather source. Three ESPN hosts are in play:
  - site.web.api.espn.com   → search (resolve any team name → sport/league/id)
  - site.api.espn.com       → scoreboards + team schedules
  - (core host unused at runtime; league slugs come from search)

Design goals (see the session that built this):
  - Answer almost any team-sports question — ANY league/team/school/nation —
    by resolving the team dynamically via search instead of a hardcoded table,
    then reading its full-season schedule (past + future, so offseason "next
    game" and "who did X lose to last season" both work).
  - Stay fast (<~3s): at most a search call + a few schedule fetches run
    concurrently.
  - Best-effort individual sports (golf, tennis, F1, UFC) via their scoreboards.
  - Everything is defensive; on any miss return None so the server falls back to
    web research.

The small keyword tables below are NOT the coverage set — they exist so the
server's synchronous fast-path (detect_action_fast) can cheaply decide "this is
a sports question" without a network call. Actual resolution is dynamic.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

import httpx

log = logging.getLogger("valet.sports")

_SITE = "https://site.api.espn.com/apis/site/v2/sports"
_SEARCH = "https://site.web.api.espn.com/apis/search/v2"
_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Keyword tables — used ONLY for the server's sync fast-path routing (deciding
# a query is sports-y) and for the World Cup nation path. Resolution proper is
# dynamic via ESPN search, so this list needn't be exhaustive.
# ---------------------------------------------------------------------------
_LEAGUES: list[tuple[str, str, str, str]] = [
    ("fifa world cup", "soccer", "fifa.world", "FIFA World Cup"),
    ("world cup", "soccer", "fifa.world", "FIFA World Cup"),
    ("champions league", "soccer", "uefa.champions", "UEFA Champions League"),
    ("europa league", "soccer", "uefa.europa", "UEFA Europa League"),
    ("premier league", "soccer", "eng.1", "Premier League"),
    ("la liga", "soccer", "esp.1", "La Liga"),
    ("serie a", "soccer", "ita.1", "Serie A"),
    ("bundesliga", "soccer", "ger.1", "Bundesliga"),
    ("ligue 1", "soccer", "fra.1", "Ligue 1"),
    ("mls", "soccer", "usa.1", "MLS"),
    ("epl", "soccer", "eng.1", "Premier League"),
    ("ucl", "soccer", "uefa.champions", "UEFA Champions League"),
    ("college football", "football", "college-football", "College Football"),
    ("cfb", "football", "college-football", "College Football"),
    ("ncaaf", "football", "college-football", "College Football"),
    ("college basketball", "basketball", "mens-college-basketball", "College Basketball"),
    ("nfl", "football", "nfl", "NFL"),
    ("nba", "basketball", "nba", "NBA"),
    ("wnba", "basketball", "wnba", "WNBA"),
    ("mlb", "baseball", "mlb", "MLB"),
    ("nhl", "hockey", "nhl", "NHL"),
]

# World Cup nations → the fifa.world scoreboard path (proven; national-team
# schedules span competitions, so the tournament scoreboard is cleaner here).
_NATIONS_WORDS = [
    "united states", "south korea", "saudi arabia", "costa rica", "new zealand",
    "ivory coast", "south africa", "czech republic",
    "argentina", "england", "france", "spain", "brazil", "portugal", "germany",
    "netherlands", "italy", "belgium", "croatia", "morocco", "uruguay",
    "colombia", "mexico", "canada", "japan", "australia", "denmark", "poland",
    "switzerland", "senegal", "ghana", "nigeria", "cameroon", "ecuador", "iran",
    "usa", "korea", "wales", "scotland",
]

# A few popular clubs/teams so the sync fast-path recognizes a bare team name
# ("did the Lakers win") as sports without a network call. Resolution still
# happens dynamically. Value is only the sport-ish hint, unused at resolve time.
_TEAM_WORDS = [
    "lakers", "celtics", "warriors", "knicks", "cowboys", "49ers", "chiefs",
    "eagles", "yankees", "dodgers", "red sox", "arsenal", "liverpool",
    "manchester", "real madrid", "barcelona", "uva", "cavaliers",
]

_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "usa": ("united states", "usa"),
    "korea": ("korea",),
}
_TEAM_DISPLAY: dict[str, str] = {"usa": "USA", "united states": "USA"}
_TAKES_THE = {
    "united states", "usa", "netherlands", "philippines", "czech republic",
    "ivory coast", "republic of ireland", "gambia",
}
_TOURNAMENTS = {"fifa.world", "uefa.champions", "uefa.europa", "uefa.euro"}


def resolve_league(query: str) -> tuple[str, str, str] | None:
    """Dict lookup for the sync fast-path only: does this name a league/team/
    nation we recognize? Returns (sport, league, display) or None. NOT the
    resolver used at answer time (that's dynamic)."""
    q = (query or "").lower()
    for kw, sport, league, name in _LEAGUES:
        if kw in q:
            return sport, league, name
    for kw in _NATIONS_WORDS:
        if kw in q:
            return "soccer", "fifa.world", "FIFA World Cup"
    for kw in _TEAM_WORDS:
        if kw in q:
            return "unknown", "unknown", kw.title()
    return None


def _match_terms(hint: str) -> tuple[str, ...]:
    return _TEAM_ALIASES.get(hint, (hint,))


def _display_name(hint: str, espn_name: str | None = None) -> str:
    if espn_name:
        return espn_name
    return _TEAM_DISPLAY.get(hint, hint.title())


def _name_matches(hint: str, name: str | None) -> bool:
    low = (name or "").lower()
    return any(term in low for term in _match_terms(hint))


def _team_phrase(name: str) -> str:
    return f"the {name}" if name.lower() in _TAKES_THE else name


def _nation_hit(q: str) -> str | None:
    for kw in sorted(_NATIONS_WORDS, key=len, reverse=True):
        if kw in q:
            return kw
    return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
async def _get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("ESPN GET failed %s %s: %s", url, params, e)
        return None


# ---------------------------------------------------------------------------
# Event normalization — handles BOTH scoreboard and team-schedule shapes.
# Score is a bare string on scoreboards ("4") but an object {value,
# displayValue[,winner]} on schedules; winner lives on the competitor (US) or
# inside the score (soccer). Status sits at ev.status OR competition.status.
# ---------------------------------------------------------------------------
def _score_str(c: dict) -> str | None:
    s = c.get("score")
    if isinstance(s, dict):
        return s.get("displayValue") or (
            str(int(s["value"])) if isinstance(s.get("value"), (int, float)) else None
        )
    return s if s not in ("", None) else None


def _is_winner(c: dict) -> bool | None:
    if c.get("winner") is not None:
        return bool(c.get("winner"))
    s = c.get("score")
    if isinstance(s, dict) and s.get("winner") is not None:
        return bool(s.get("winner"))
    return None


def _norm(ev: dict) -> dict | None:
    try:
        comp = (ev.get("competitions") or [{}])[0]
        status = ((ev.get("status") or comp.get("status") or {}).get("type")) or {}
        home = away = None
        for c in comp.get("competitors") or []:
            side = {
                "name": (c.get("team") or {}).get("displayName")
                or (c.get("team") or {}).get("name") or "TBD",
                "score": _score_str(c),
                "winner": _is_winner(c),
            }
            if c.get("homeAway") == "home":
                home = side
            else:
                away = side
        return {
            "id": ev.get("id"),
            "short_name": ev.get("shortName"),
            "date": ev.get("date"),
            "state": status.get("state"),  # pre | in | post
            "detail": status.get("shortDetail") or status.get("detail"),
            "detail_full": status.get("detail"),
            # Knockout round slug (fifa.world etc.): "final", "3rd-place-match",
            # "semifinals", … — lets us name "the final" vs "the third-place match".
            "round": (ev.get("season") or {}).get("slug"),
            "home": home,
            "away": away,
        }
    except Exception as e:
        log.debug("normalize failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# League scoreboard path (for "NBA scores", "World Cup scores", tournaments)
# ---------------------------------------------------------------------------
async def _fetch_scoreboard(sport: str, league: str, date_range: str | None = None) -> list[dict] | None:
    data = await _get_json(f"{_SITE}/{sport}/{league}/scoreboard",
                           {"dates": date_range} if date_range else None)
    if data is None:
        return None
    out = [n for ev in (data.get("events") or []) if (n := _norm(ev))]
    out.sort(key=lambda e: e.get("date") or "")
    return out


def _win(a: int, b: int) -> str:
    t = datetime.now()
    return (f"{(t + timedelta(days=a)).strftime('%Y%m%d')}"
            f"-{(t + timedelta(days=b)).strftime('%Y%m%d')}")


async def _fetch_league_events(sport: str, league: str) -> list[dict]:
    """Tournaments: two sub-cap windows (recent + history) merged — ESPN caps a
    single response at ~100 events and would drop the current knockout games.
    Domestic leagues: one near window, falling back to the current slate."""
    if league in _TOURNAMENTS:
        recent, history = await asyncio.gather(
            _fetch_scoreboard(sport, league, _win(-17, 16)),
            _fetch_scoreboard(sport, league, _win(-72, -17)),
        )
        merged: dict[str, dict] = {}
        for e in (history or []) + (recent or []):
            merged[e.get("id")] = e
        out = list(merged.values())
        out.sort(key=lambda e: e.get("date") or "")
        return out
    events = await _fetch_scoreboard(sport, league, _win(-3, 14))
    if not events:
        events = await _fetch_scoreboard(sport, league) or []
    return events


# ---------------------------------------------------------------------------
# Dynamic team resolution via ESPN search
# ---------------------------------------------------------------------------
_SEARCH_STOP = {
    "who", "did", "what", "was", "were", "the", "score", "of", "in", "when",
    "do", "does", "is", "are", "next", "last", "game", "match", "play",
    "playing", "against", "lose", "lost", "win", "won", "beat", "record",
    "season", "year", "this", "vs", "versus", "how", "their", "a", "to",
    "for", "and", "results", "result", "standings", "schedule", "fixture",
    "fixtures", "tonight", "today", "yesterday", "s",
}
_SPORT_WORDS = {
    "football": "football", "soccer": "soccer", "basketball": "basketball",
    "baseball": "baseball", "hockey": "hockey",
}


def _clean_for_search(query: str) -> str:
    toks = [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if t]
    kept = [t for t in toks if t not in _SEARCH_STOP]
    return " ".join(kept) if kept else " ".join(toks)


async def _search_teams(q: str) -> list[dict]:
    data = await _get_json(_SEARCH, {"query": q, "limit": 10})
    if not data:
        return []
    teams: list[dict] = []
    for grp in data.get("results", []):
        if grp.get("type") != "team":
            continue
        for c in grp.get("contents", []):
            uid = c.get("uid", "") or ""
            m = re.search(r"t:(\d+)", uid)
            if not (c.get("sport") and c.get("defaultLeagueSlug") and m):
                continue
            teams.append({
                "sport": c.get("sport"),
                "league": c.get("defaultLeagueSlug"),
                "team_id": m.group(1),
                "name": c.get("displayName") or "",
                "subtitle": c.get("subtitle") or "",
            })
    return teams


_SPORT_PRIORITY = {"football": 5, "basketball": 4, "baseball": 3, "hockey": 2, "soccer": 1}


async def _resolve_team(query: str) -> dict | None:
    """Resolve a free-text query to a specific team via ESPN search. Returns
    {sport, league, team_id, name} or None (zero candidates). Uses a shrinking
    token ladder so trailing non-name words ("...make the playoffs") don't break
    the match, then ranks by sport-word match → name overlap → sport priority
    (so a bare "uva" defaults to football over the basketball rows)."""
    cleaned = _clean_for_search(query)
    if not cleaned:
        return None
    toks = cleaned.split()
    cands: list[dict] = []
    tried: set[str] = set()
    for n in (len(toks), 2, 1):
        if n < 1 or n > len(toks):
            continue
        term = " ".join(toks[:n])
        if term in tried:
            continue
        tried.add(term)
        cands = await _search_teams(term)
        if cands:
            break
    if not cands:  # last try: drop sport words
        stripped = " ".join(t for t in toks if t not in _SPORT_WORDS)
        if stripped and stripped not in tried:
            cands = await _search_teams(stripped)
    if not cands:
        return None

    ql = (query or "").lower()
    sport_hint = next((s for w, s in _SPORT_WORDS.items() if w in ql), None)

    def score(c: dict) -> tuple:
        name_toks = [t for t in re.split(r"[^a-z0-9]+", c["name"].lower())
                     if len(t) > 2 and t not in _SEARCH_STOP]
        overlap = sum(1 for t in name_toks if t in ql)
        sport_match = 1 if (sport_hint and c["sport"] == sport_hint) else 0
        return (sport_match, overlap, _SPORT_PRIORITY.get(c["sport"], 0))

    best = max(cands, key=score)
    return {k: best[k] for k in ("sport", "league", "team_id", "name")}


def _season_guesses(query: str) -> list[int]:
    """Which season years to pull for a team schedule. ESPN's season labeling is
    quirky and offset for soccer, so pull a small span and merge. 'last
    season/year' shifts back one."""
    y = datetime.now().year
    if any(w in query.lower() for w in ("last season", "last year", "previous season")):
        return [y, y - 1]
    return [y + 1, y, y - 1]


async def _fetch_team_events(sport: str, league: str, team_id: str, query: str) -> list[dict]:
    seasons = _season_guesses(query)
    results = await asyncio.gather(*[
        _get_json(f"{_SITE}/{sport}/{league}/teams/{team_id}/schedule", {"season": y})
        for y in seasons
    ])
    merged: dict[str, dict] = {}
    for data in results:
        for ev in (data or {}).get("events", []) or []:
            n = _norm(ev)
            if n and n.get("id"):
                merged[n["id"]] = n
    out = list(merged.values())
    out.sort(key=lambda e: e.get("date") or "")
    return out


# ---------------------------------------------------------------------------
# Buckets + summaries (shared by league, team, and nation paths)
# ---------------------------------------------------------------------------
def _bucket(events: list[dict]) -> dict[str, list[dict]]:
    return {
        "live": [e for e in events if e.get("state") == "in"][:6],
        "recent": [e for e in events if e.get("state") == "post"][-5:],
        "upcoming": [e for e in events if e.get("state") == "pre"][:6],
    }


def _score_line(e: dict) -> str:
    home, away = e.get("home") or {}, e.get("away") or {}
    hn, an = home.get("name", "TBD"), away.get("name", "TBD")
    if e.get("state") == "pre":
        return f"{an} vs {hn}"
    hs, as_ = home.get("score"), away.get("score")
    if hs is not None and as_ is not None:
        return f"{an} {as_}, {hn} {hs}"
    return f"{an} vs {hn}"


def _fmt_when(iso: str | None, detail_full: str | None) -> str:
    if detail_full and re.search(r"\d", detail_full):
        return detail_full
    if not iso:
        return "TBD"
    try:
        dt = datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M%z")
        return dt.astimezone().strftime("%A %B %-d at %-I:%M %p")
    except Exception:
        return iso


def _sides_for(g: dict, hint: str) -> tuple[dict | None, dict | None]:
    for a, b in (("home", "away"), ("away", "home")):
        s, o = g.get(a) or {}, g.get(b) or {}
        if _name_matches(hint, s.get("name")):
            return s, o
    return None, None


def _record(posts: list[dict], hint: str) -> tuple[int, int, int]:
    w = l = d = 0
    for g in posts:
        s, o = _sides_for(g, hint)
        if not (s and o):
            continue
        if s.get("winner"):
            w += 1
        elif o.get("winner"):
            l += 1
        else:
            d += 1
    return w, l, d


def _team_result_line(display: str, buckets: dict, hint: str, q: str) -> str | None:
    posts = buckets["recent"]
    if not posts:
        return None
    # "record last season" → summarize W-L across ALL completed games we have.
    if "record" in q:
        w, l, d = _record(posts, hint)
        subj = _team_phrase(_display_name(hint))
        rec = f"{w} and {l}" + (f" and {d}" if d else "")
        line = f"{subj} went {rec} in the games I have, sir."
        return line[0].upper() + line[1:]

    want_loss = any(w in q for w in ("lose", "lost", "knocked out", "eliminat"))
    want_win = (not want_loss) and any(w in q for w in ("beat", "defeat"))

    def lost(g):
        _, o = _sides_for(g, hint)
        return bool(o and o.get("winner"))

    def won(g):
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


def _recent_line(display: str, buckets: dict) -> str | None:
    if buckets["recent"]:
        return f"{display}, sir. Final: {_score_line(buckets['recent'][-1])}."
    return None


_ROUND_LABELS = {
    "final": "the final",
    "3rd-place-match": "the third-place match",
    "third-place-match": "the third-place match",
    "semifinals": "a semifinal",
    "quarterfinals": "a quarterfinal",
    "round-of-16": "the round of 16",
}


def _round_label(slug: str | None) -> str | None:
    return _ROUND_LABELS.get((slug or "").lower())


def _upcoming_line(display: str, buckets: dict) -> str | None:
    if buckets["upcoming"]:
        parts = []
        for e in buckets["upcoming"][:3]:
            rl = _round_label(e.get("round"))
            suffix = f" ({rl})" if rl else ""
            parts.append(f"{_score_line(e)}{suffix} — {_fmt_when(e.get('date'), e.get('detail_full'))}")
        lead = "Next up" if len(parts) == 1 else "Upcoming fixtures"
        return f"{display}, sir. {lead}: " + "; ".join(parts) + "."
    return None


def _summary(display: str, buckets: dict, query: str, hint: str | None) -> str:
    q = (query or "").lower()
    if buckets["live"]:
        e = buckets["live"][0]
        return f"{display}, sir: {_score_line(e)}, {e.get('detail') or 'in progress'}."
    # "who is playing in the final/next game" is a MATCHUP question → the
    # upcoming fixture, even though "final" would otherwise read as a result.
    if any(w in q for w in ("who is playing", "who's playing", "whos playing",
                            "playing in", "who is in", "who's in", "who plays", "matchup")):
        up = buckets["upcoming"]
        # If a specific round is named, pinpoint it ("the final" → the final).
        want = None
        if "final" in q and "semi" not in q:
            want = "final"
        elif "third place" in q or "3rd place" in q:
            want = "3rd-place-match"
        elif "semi" in q:
            want = "semifinals"
        if want and up:
            m = next((e for e in up if (e.get("round") or "").lower() == want), None)
            if m:
                rl = (_round_label(want) or "the match").capitalize()
                return (f"{display}, sir. {rl}: {_score_line(m)} — "
                        f"{_fmt_when(m.get('date'), m.get('detail_full'))}.")
        return (_upcoming_line(display, buckets) or _recent_line(display, buckets)
                or f"I found no upcoming {display} fixtures, sir.")
    result_intent = any(w in q for w in (
        "score", "did ", "won", " win", "beat", "result", "final", "how did",
        "was the", "lose", "lost", "outcome", "knocked out", "record"))
    schedule_intent = any(w in q for w in (
        "when", "what time", "next", "upcoming", "schedule", "fixture", "play",
        "kick", "start"))

    # Pure schedule question: lead with the next fixture; if none is on the
    # calendar yet (offseason), say so honestly rather than reading a result.
    if schedule_intent and not result_intent:
        up = _upcoming_line(display, buckets)
        if up:
            return up
        if buckets["recent"]:
            return (f"{display}, sir — nothing on the schedule yet. "
                    f"Last time out: {_score_line(buckets['recent'][-1])}.")
        return f"I found no upcoming {display} fixtures, sir."

    if result_intent:
        if hint:
            line = _team_result_line(display, buckets, hint, q)
            if line:
                return line
        return _recent_line(display, buckets) or _upcoming_line(display, buckets) \
            or f"I found no recent {display} results, sir."

    return _upcoming_line(display, buckets) or _recent_line(display, buckets) \
        or f"I found no current {display} fixtures, sir."


def _card(display: str, buckets: dict) -> dict:
    def cell(e):
        when = _fmt_when(e.get("date"), e.get("detail_full")) if e.get("state") == "pre" else e.get("detail")
        return {"matchup": _score_line(e), "state": e.get("state"), "detail": when,
                "date": e.get("date"), "home": e.get("home"), "away": e.get("away")}
    return {
        "league": display,
        "live": [cell(e) for e in buckets["live"]],
        "recent": [cell(e) for e in buckets["recent"]],
        "upcoming": [cell(e) for e in buckets["upcoming"]],
        "updated_at": datetime.now().strftime("%-I:%M %p"),
    }


def _empty_result(display: str, msg: str) -> dict:
    return {"payload": _card(display, {"live": [], "recent": [], "upcoming": []}), "summary": msg}


# ---------------------------------------------------------------------------
# Individual sports (best-effort): golf, tennis, F1, MMA
# ---------------------------------------------------------------------------
_INDIVIDUAL = [
    (("golf", "pga", "masters", "the open", "ryder cup", "leaderboard"), "golf", "pga", "Golf"),
    (("tennis", "wimbledon", "french open", "roland garros", "atp", "wta", "australian open"), "tennis", "atp", "Tennis"),
    (("formula 1", "formula one", "f1", "grand prix", "grand-prix"), "racing", "f1", "Formula 1"),
    (("ufc", "mma"), "mma", "ufc", "UFC"),
]


def _detect_individual(q: str) -> tuple[str, str, str] | None:
    for kws, sport, league, display in _INDIVIDUAL:
        if any(k in q for k in kws):
            return sport, league, display
    return None


async def _individual_answer(ind: tuple[str, str, str], query: str) -> dict | None:
    sport, league, display = ind
    data = await _get_json(f"{_SITE}/{sport}/{league}/scoreboard")
    if data is None:
        return None
    events = data.get("events") or []
    if not events:
        return _empty_result(display, f"I found no current {display} events, sir.")
    ev = events[0]
    name = ev.get("name") or display
    st = (ev.get("status") or {}).get("type") or {}
    state, detail = st.get("state"), (st.get("detail") or st.get("shortDetail") or "")

    if sport == "golf":
        comp = (ev.get("competitions") or [{}])[0]
        comps = sorted(comp.get("competitors") or [], key=lambda c: c.get("order", 999))
        leader = comps[0] if comps else None
        if leader and state != "pre":
            who = (leader.get("athlete") or {}).get("displayName", "the leader")
            summary = f"{name}, sir: {who} leads at {leader.get('score', 'even')}."
        else:
            summary = f"{name}, sir — {detail or 'scheduled'}."
        rows = [{"matchup": (c.get("athlete") or {}).get("displayName", "?"),
                 "detail": str(c.get("score", "")), "state": state}
                for c in comps[:8]]
    elif sport == "mma":
        # The event name already carries the main event ("UFC ...: A vs. B").
        comp = ev.get("competitions") or []
        summary = f"{name}, sir — {_fmt_when(ev.get('date'), detail)}." if state == "pre" \
            else f"{name}, sir — {detail or 'in progress'}."
        rows = []
        for b in comp[:8]:
            fs = [(c.get("athlete") or {}).get("displayName", "?") for c in (b.get("competitors") or [])]
            rows.append({"matchup": " vs ".join(fs), "detail": ((b.get("status") or {}).get("type") or {}).get("shortDetail", ""), "state": state})
    elif sport == "tennis":
        summary = f"{name}, sir — {detail or state or 'in progress'}."
        rows = []
    else:  # racing / F1
        summary = f"{name}, sir — {_fmt_when(ev.get('date'), detail)}." if state == "pre" \
            else f"{name}, sir — {detail or 'in progress'}."
        rows = []

    payload = {"league": display, "live": [] if state != "in" else rows,
               "recent": rows if state == "post" else [],
               "upcoming": rows if state == "pre" else ([] if state == "post" else rows),
               "updated_at": datetime.now().strftime("%-I:%M %p")}
    return {"payload": payload, "summary": summary}


# ---------------------------------------------------------------------------
# World Cup nation path (scoreboard, proven)
# ---------------------------------------------------------------------------
async def _nation_answer(nation: str, query: str) -> dict | None:
    events = await _fetch_league_events("soccer", "fifa.world")
    if not events:
        return None
    filtered = [e for e in events
                if _name_matches(nation, (e.get("home") or {}).get("name"))
                or _name_matches(nation, (e.get("away") or {}).get("name"))]
    if not filtered:
        return _empty_result("FIFA World Cup",
                             f"I found no recent or upcoming {_display_name(nation)} fixtures, sir.")
    b = _bucket(filtered)
    return {"payload": _card("FIFA World Cup", b), "summary": _summary("FIFA World Cup", b, query, nation)}


# ---------------------------------------------------------------------------
# Team path (dynamic search → schedule)
# ---------------------------------------------------------------------------
async def _team_answer(team: dict, query: str) -> dict | None:
    events = await _fetch_team_events(team["sport"], team["league"], team["team_id"], query)
    display = team["name"]
    if not events:
        return _empty_result(display, f"I found no recent or upcoming {display} games, sir.")
    b = _bucket(events)
    # Filter hint = the team's distinctive last name token ("Cavaliers", "Lakers").
    hint = (team["name"].split()[-1] or team["name"]).lower()

    # "Record" is per-SEASON, so count exactly one season's completed games
    # (the merged multi-season `events` above would sum several seasons).
    if "record" in query.lower():
        y = datetime.now().year
        last = any(k in query.lower() for k in ("last season", "last year", "previous"))
        season = y - 1 if last else y
        data = await _get_json(
            f"{_SITE}/{team['sport']}/{team['league']}/teams/{team['team_id']}/schedule",
            {"season": season})
        posts = [n for ev in (data or {}).get("events", []) or []
                 if (n := _norm(ev)) and n.get("state") == "post"]
        w, l, d = _record(posts, hint)
        # Only soccer draws are real; a "draw" elsewhere is a data gap — drop it.
        rec = f"{w} and {l}" + (f" and {d}" if d and team["sport"] == "soccer" else "")
        if not posts:
            summary = f"I haven't the {display} record for that season, sir."
        elif last:
            summary = f"{display} went {rec} last season, sir."
        else:
            summary = f"{display} are {rec} so far this season, sir."
        return {"payload": _card(display, b), "summary": summary}

    return {"payload": _card(display, b), "summary": _summary(display, b, query, hint)}


# ---------------------------------------------------------------------------
# League-only detection ("nba scores", "world cup scores")
# ---------------------------------------------------------------------------
_CUE_WORDS = {
    "scores", "score", "results", "result", "standings", "schedule", "today",
    "tonight", "yesterday", "this", "week", "the", "of", "in", "who", "is",
    "winning", "whats", "what", "s", "games", "game", "and", "whos", "playing",
    "final", "finals", "semifinal", "semifinals", "semis", "quarterfinal",
    "quarterfinals", "playoff", "playoffs", "table", "standing", "next", "on",
    "third", "place", "match", "matchup", "semi", "quarter", "plays", "are",
    "upcoming", "fixtures", "fixture",
}


def _league_only_hit(q: str) -> tuple[str, str, str] | None:
    """A query that names a league and little else ('NBA scores') → league
    scoreboard. If a specific team seems named, this returns None so the team
    path runs instead."""
    for kw, sport, league, name in _LEAGUES:
        if kw in q:
            rest = q.replace(kw, " ")
            leftover = [t for t in re.split(r"[^a-z0-9]+", rest) if t and t not in _CUE_WORDS]
            if not leftover:
                return sport, league, name
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
async def get_sports(query: str) -> dict | None:
    """Resolve and answer a sports question. Returns {payload, summary} or None
    (→ server falls back to web research)."""
    q = (query or "").lower()

    ind = _detect_individual(q)
    if ind:
        try:
            return await _individual_answer(ind, query)
        except Exception as e:
            log.warning("individual sport answer failed: %s", e)
            return None

    nation = _nation_hit(q)
    if nation:
        try:
            r = await _nation_answer(nation, query)
            if r:
                return r
        except Exception as e:
            log.warning("nation answer failed: %s", e)

    lg = _league_only_hit(q)
    if lg:
        try:
            events = await _fetch_league_events(lg[0], lg[1])
            if events:
                b = _bucket(events)
                return {"payload": _card(lg[2], b), "summary": _summary(lg[2], b, query, None)}
        except Exception as e:
            log.warning("league answer failed: %s", e)

    try:
        team = await _resolve_team(query)
        if team:
            r = await _team_answer(team, query)
            if r:
                return r
    except Exception as e:
        log.warning("team resolution failed: %s", e)

    # Last resort: any recognized league keyword → its scoreboard.
    dl = resolve_league(query)
    if dl and dl[0] != "unknown":
        try:
            events = await _fetch_league_events(dl[0], dl[1])
            if events:
                b = _bucket(events)
                return {"payload": _card(dl[2], b), "summary": _summary(dl[2], b, query, None)}
        except Exception as e:
            log.warning("fallback league answer failed: %s", e)

    return None
