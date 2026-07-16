"""Live sports STATISTICS via StatMuse's keyless ask endpoint.

Keyless, like weather.py (Open-Meteo) and sports.py (ESPN). Powers
[ACTION:STATS].

Why this exists: ESPN (sports.py) answers scores / schedules / standings, but
NOT individual player or team season/tournament statistics. Those questions —
"how many goals did Mbappé score for Real Madrid this season", "who's the
Premier League top scorer", "LeBron's points per game" — were previously
answered wrong (SPORTS returned an unrelated match score) or slowly (the
multi-page RESEARCH crawl). StatMuse is a purpose-built natural-language sports
stats engine that returns ONE clean, correct sentence per question.

How it works: StatMuse's `/ask/<slug>` endpoint auto-detects the sport (soccer,
NBA, NFL, MLB, NHL, …) from the question itself — no sport prefix or key needed
— and embeds the direct answer in the page's `og:description` meta tag, which
lives in the <head>, so we stream the response and stop as soon as we've read
it (typically <1.5s, a fraction of the page). It understands relative phrasing
("this season", "last season") natively.

Defensive by design: on any miss — a non-sports question, an unparseable page,
StatMuse's generic homepage fallback, or a network error — return None so the
server falls back to the quick web LOOKUP (and then deep RESEARCH). Modeled on
the "return None → server falls back" contract sports.py uses.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx

log = logging.getLogger("valet.statmuse")

_ASK = "https://www.statmuse.com/ask"
_TIMEOUT = 8.0
# A realistic desktop UA — StatMuse serves the og:description answer to browser
# user-agents; a bare python-httpx UA can get a thinner page.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# StatMuse serves this boilerplate description on its homepage / when it can't
# answer a question (it redirects unparseable asks to a generic page). If the
# meta description starts with this, treat it as "no answer" and fall back.
_GENERIC_PREFIXES = (
    "instant answers to your",
    "statmuse is a search engine",
)

_OG_RE = re.compile(r'<meta property="og:description" content="([^"]*)"')
_META_RE = re.compile(r'<meta name="description" content="([^"]*)"')

# ---------------------------------------------------------------------------
# Intent detection — used ONLY by the server's sync fast-path (detect_action_fast)
# to cheaply decide "this is a sports-STAT question" without a network call. Not
# the coverage set: any question routed here that StatMuse can't answer falls
# back cleanly. Kept deliberately sports-flavored so non-sports "how many…"
# ("how many countries in Africa") is NOT grabbed here.
# ---------------------------------------------------------------------------

# Leader / superlative phrasings that don't say "how many" but are pure stat
# questions ("who's the top scorer", "golden boot leader", "most goals").
STAT_LEADER_CUES = (
    "top scorer", "leading scorer", "top scorers", "leading scorers",
    "golden boot", "leading goalscorer", "top goalscorer", "leading goal scorer",
    "most goals", "most points", "most assists", "most rebounds", "most home runs",
    "most touchdowns", "most yards", "most wins", "most saves", "most tackles",
    "scoring leader", "assist leader", "assists leader", "rebound leader",
    "batting average", "home run leader", "passing yards", "rushing yards",
    "receiving yards", "points per game", "goals per game", "assists per game",
    "rebounds per game", "strikeouts", "era leader", "mvp",
    "who scored the most", "who has the most", "who leads the",
)

# Stat nouns (word-boundary matched). Paired with a "how many / how much / how
# far" quantifier by the server's fast-path. Also used to sanity-gate the
# leader cues to sport contexts.
STAT_NOUNS = (
    "goals", "points", "assists", "rebounds", "touchdowns", "home runs",
    "homers", "wickets", "saves", "tackles", "yards", "caps", "appearances",
    "wins", "titles", "championships", "hat tricks", "clean sheets",
    "strikeouts", "interceptions", "three pointers", "goalscorers",
)


def looks_like_stat_query(query: str) -> bool:
    """True when the query reads like a sports statistic — a leader/superlative
    phrasing ("top scorer", "most goals"), or a "how many <stat-noun>" count.
    Conservative: the caller may still route generously since a miss falls back.
    """
    q = (query or "").lower()
    if any(cue in q for cue in STAT_LEADER_CUES):
        return True
    if ("how many" in q or "how much" in q or "how far" in q) and \
            any(re.search(rf"\b{re.escape(n)}\b", q) for n in STAT_NOUNS):
        return True
    return False


def _slug(query: str) -> str:
    """StatMuse accepts the natural question as a hyphenated path segment."""
    q = re.sub(r"[^\w\s-]", " ", (query or "").lower())
    q = re.sub(r"\s+", "-", q.strip())
    return quote(q, safe="-")


def _clean(query: str) -> str:
    """Strip wake words / filler so the slug is a clean question."""
    q = (query or "").strip()
    q = re.sub(r"^(hey\s+|ok\s+)?(vee|valet)[,\s]+", "", q, flags=re.IGNORECASE)
    q = re.sub(r"^(can|could|would|will|please)\s+you\s+", "", q, flags=re.IGNORECASE)
    return q.strip(" .?!")


async def get_stat(query: str) -> dict | None:
    """Ask StatMuse and return {"answer": str, "url": str} or None on any miss.

    Streams the response and stops once the <head> meta description is in hand,
    so a large results page doesn't cost a large download.
    """
    q = _clean(query)
    if not q:
        return None
    url = f"{_ASK}/{_slug(q)}"
    try:
        buf = ""
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True,
                                     headers={"User-Agent": _UA}) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    log.info("statmuse %s -> HTTP %s", url, r.status_code)
                    return None
                final_url = str(r.url)
                async for chunk in r.aiter_text():
                    buf += chunk
                    # og:description lives in <head>; stop as soon as it closes.
                    m = _OG_RE.search(buf)
                    if m:
                        break
                    if len(buf) > 120_000:  # safety cap — never read a whole page
                        break
    except Exception as e:
        log.warning("statmuse fetch failed %s: %s", url, e)
        return None

    m = _OG_RE.search(buf) or _META_RE.search(buf)
    if not m:
        return None
    answer = (m.group(1) or "").strip()
    if not answer:
        return None
    low = answer.lower()
    if any(low.startswith(p) for p in _GENERIC_PREFIXES):
        log.info("statmuse generic fallback for %r — no stat answer", q)
        return None
    return {"answer": answer, "url": final_url}
