"""Shared best-effort page-metadata fetcher.

Two callers today:

  - `server._execute_native_research` — fires one preview fetch per
    `web_fetch_tool_result` so the Process Panel can render live
    source-preview cards while research is in flight.

  - `claude_middleware.extract_and_emit` — after Haiku extracts product
    cards from the assistant response, fills in `image_url` from the
    source page's og:image when the model didn't supply one.

Both callers tolerate failure. The fetch is capped at 1.5s by default
(callers can override); on any network error, non-HTML response, or
missing tag the corresponding fields stay `None`. Never raises.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger("jarvis.page_preview")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 JARVIS-Preview/0.1"
)

_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE | re.DOTALL)
_OG_IMAGE_RE_A = re.compile(
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE_B = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
    re.IGNORECASE,
)
_TWITTER_IMAGE_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


async def fetch_page_preview(url: str, timeout: float = 1.5) -> dict[str, Any]:
    """Fetch a page and return its preview metadata. Never raises.

    Returns a dict with keys: `url`, `hostname`, `title`, `og_image_url`.
    Only `url` and `hostname` are guaranteed to be set; `title` and
    `og_image_url` are `None` when the corresponding tag is missing,
    the page can't be fetched, or the response isn't HTML.
    """
    parsed = urlparse(url)
    out: dict[str, Any] = {
        "url": url,
        "hostname": parsed.hostname or "",
        "title": None,
        "og_image_url": None,
    }
    if not parsed.scheme.startswith("http"):
        return out
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.5"},
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            # Only need the <head>. Some sites serve megabyte HTML so cap.
            head = r.text[:65536]

        t = _TITLE_RE.search(head)
        if t:
            out["title"] = re.sub(r"\s+", " ", t.group(1)).strip()[:200]

        og = _OG_IMAGE_RE_A.search(head) or _OG_IMAGE_RE_B.search(head) or _TWITTER_IMAGE_RE.search(head)
        if og:
            img = og.group(1)
            if img.startswith("//"):
                img = f"{parsed.scheme}:{img}"
            elif img.startswith("/"):
                img = urljoin(f"{parsed.scheme}://{parsed.hostname}", img)
            out["og_image_url"] = img
    except Exception as e:
        log.debug(f"page_preview fetch failed for {url}: {e}")
    return out
