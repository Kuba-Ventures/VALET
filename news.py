"""Top headlines and topic news via Google News RSS — keyless, fast (~0.3s).

Mirrors weather.py / sports.py / markets.py: async httpx, errors→None, a
build_card_payload() the frontend renders plus a format_summary() the butler
speaks. No API key: Google News exposes public RSS for the top slate and for
any topic query.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

log = logging.getLogger("valet.news")

_TOP = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
_SEARCH = "https://news.google.com/rss/search"
_TIMEOUT = 8.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Phrases that signal a news request (server sync fast-path gate).
NEWS_CUES = (
    "news", "headlines", "headline", "what's happening", "whats happening",
    "what is happening", "latest on", "latest with", "going on", "top stories",
    "top story", "current events",
)

# Stripped when extracting a topic from the utterance. If nothing meaningful
# remains → top headlines; otherwise search the remainder.
_TOPIC_STOP = {
    "what", "whats", "what's", "is", "the", "news", "headlines", "headline",
    "latest", "on", "about", "happening", "with", "tell", "me", "any", "give",
    "today", "top", "current", "story", "stories", "update", "updates", "going",
    "in", "right", "now", "s", "of", "a", "hows", "recent", "get", "some",
    "please", "anything", "for",
}


def _extract_topic(query: str) -> str | None:
    toks = [t for t in re.split(r"[^a-z0-9&]+", (query or "").lower()) if t and t not in _TOPIC_STOP]
    return " ".join(toks) if toks else None


def _clean_title(title: str, source: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", title or "")).strip()
    # Google appends " - Source" to titles; drop it (source is shown separately).
    if source and t.endswith(f" - {source}"):
        t = t[: -len(f" - {source}")].strip()
    else:
        t = re.sub(r"\s+-\s+[^-]{2,40}$", "", t).strip()
    return t


async def fetch_news(query: str | None = None, n: int = 6) -> list[dict] | None:
    """Return up to `n` items [{title, source, link}] for a topic, or the top
    slate when query is None. None on failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            if query:
                r = await client.get(_SEARCH, params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
            else:
                r = await client.get(_TOP)
            r.raise_for_status()
            body = r.text
    except Exception as e:
        log.warning("news fetch failed for %r: %s", query, e)
        return None
    items: list[dict] = []
    for block in re.findall(r"<item>(.*?)</item>", body, re.S)[:n]:
        tm = re.search(r"<title>(.*?)</title>", block, re.S)
        sm = re.search(r"<source[^>]*>(.*?)</source>", block, re.S)
        lm = re.search(r"<link>(.*?)</link>", block, re.S)
        source = html.unescape(sm.group(1)).strip() if sm else ""
        title = _clean_title(tm.group(1) if tm else "", source)
        if title:
            items.append({"title": title, "source": source, "link": (lm.group(1).strip() if lm else "")})
    return items


def format_summary(topic: str | None, items: list[dict]) -> str:
    if not items:
        what = f"on {topic}" if topic else "in the headlines"
        return f"I found nothing {what} just now, sir."
    lead = f"On {topic}, sir" if topic else "Today's top headlines, sir"
    heads = [it["title"] for it in items[:3]]
    return f"{lead}: " + " … ".join(heads) + "."


def build_card_payload(topic: str | None, items: list[dict]) -> dict:
    return {
        "topic": topic or "Top headlines",
        "items": [{"title": it["title"], "source": it["source"], "link": it["link"]} for it in items[:6]],
    }


async def get_news(query: str) -> dict | None:
    """Top-level: top headlines, or news on a topic extracted from the query.
    Returns {payload, summary} or None on failure."""
    topic = _extract_topic(query)
    items = await fetch_news(topic)
    if items is None:
        return None
    return {"payload": build_card_payload(topic, items), "summary": format_summary(topic, items)}
