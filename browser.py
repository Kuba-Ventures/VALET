"""
JARVIS Browser — Playwright-based web browsing capabilities.

Provides search, page visits, screenshots, and multi-step research.
Runs headless Chromium with realistic user agent to avoid blocking.

When called with a `task_id`, each navigation and screenshot is announced
on the process event bus so the panel shows live progress.
"""

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from process_events import (
    emit_browser_action,
    emit_screenshot,
    emit_step,
)

log = logging.getLogger("jarvis.browser")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_MS = 30_000


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"


async def _capture_panel_screenshot(page, task_id: str, label: str) -> str | None:
    """Save a screenshot of `page` to data/screenshots/<task_id>/<label>-<ts>.png.

    Returns the path RELATIVE to data/screenshots/ (e.g. "abc12345/results-...png")
    so the frontend can fetch it via /screenshots/<rel>. Returns None on failure
    or when no task_id is supplied.
    """
    if not task_id:
        return None
    try:
        base = SCREENSHOTS_DIR / task_id
        base.mkdir(parents=True, exist_ok=True)
        filename = f"{label}-{int(time.time() * 1000)}.png"
        full_path = base / filename
        await page.screenshot(path=str(full_path), full_page=False)
        return f"{task_id}/{filename}"
    except Exception as e:
        log.warning(f"panel screenshot capture failed: {e}")
        return None


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PageContent:
    title: str
    url: str
    text_content: str
    word_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResearchResult:
    topic: str
    sources: list[str]
    summary: str
    key_findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Browser Manager
# ---------------------------------------------------------------------------

class JarvisBrowser:
    """Playwright-based web browsing for JARVIS."""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._context = None

    async def _ensure_browser(self):
        """Launch browser if not running."""
        if self._browser and self._context:
            return

        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        # Launch VISIBLE browser so user can watch JARVIS browse
        self._browser = await self._pw.chromium.launch(headless=False)
        self._context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        log.info("Browser launched (visible Chromium)")

    async def _new_page(self):
        """Create a new page in the browser context."""
        await self._ensure_browser()
        return await self._context.new_page()

    # -- Search ----------------------------------------------------------------

    async def search(self, query: str, task_id: str | None = None) -> list[SearchResult]:
        """Search DuckDuckGo and return top results."""
        page = await self._new_page()
        results = []

        try:
            search_url = f"https://html.duckduckgo.com/html/?q={query}"
            if task_id:
                await emit_browser_action(task_id, f"Search: {query}", url=search_url)
            await page.goto(
                search_url,
                timeout=TIMEOUT_MS,
                wait_until="domcontentloaded",
            )

            # Extract search results from DDG HTML version
            raw = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('.result');
                    return Array.from(items).slice(0, 5).map(item => ({
                        title: (item.querySelector('.result__title a') || item.querySelector('.result__a'))?.textContent?.trim() || '',
                        url: (item.querySelector('.result__title a') || item.querySelector('.result__a'))?.href || '',
                        snippet: item.querySelector('.result__snippet')?.textContent?.trim() || ''
                    }));
                }
            """)

            for r in raw:
                if r.get("title") and r.get("url"):
                    results.append(SearchResult(
                        title=r["title"],
                        url=r["url"],
                        snippet=r.get("snippet", ""),
                    ))

            log.info(f"Search '{query}' returned {len(results)} results")
            if task_id:
                await emit_step(
                    task_id,
                    f"Got {len(results)} results",
                    detail=", ".join(r.title[:60] for r in results[:3]),
                    status="done",
                )
                rel = await _capture_panel_screenshot(page, task_id, "search-results")
                if rel:
                    await emit_screenshot(task_id, rel, caption=f"Results for '{query}'")
            # Let user see the search results for a moment
            await asyncio.sleep(2)
        except Exception as e:
            log.warning(f"Search failed for '{query}': {e}")
        finally:
            # Don't close the page — keep it visible
            pass

        return results

    # -- Visit URL -------------------------------------------------------------

    async def visit(self, url: str, task_id: str | None = None) -> PageContent:
        """Visit a URL and extract main text content."""
        page = await self._new_page()

        try:
            if task_id:
                await emit_browser_action(task_id, "Visit page", url=url)
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

            data = await page.evaluate("""
                () => {
                    const title = document.title || '';

                    // Try to get main content area first
                    const main = document.querySelector('main')
                        || document.querySelector('article')
                        || document.querySelector('[role="main"]')
                        || document.body;

                    // Remove noise elements
                    const clone = main.cloneNode(true);
                    for (const el of clone.querySelectorAll(
                        'script, style, nav, header, footer, aside, .sidebar, .menu, .ad, .advertisement, iframe'
                    )) {
                        el.remove();
                    }

                    const text = clone.innerText || clone.textContent || '';
                    // Trim to reasonable size
                    const trimmed = text.substring(0, 5000).trim();
                    return {
                        title: title,
                        text: trimmed,
                    };
                }
            """)

            text = data.get("text", "")
            if task_id:
                await emit_step(
                    task_id,
                    f"Loaded {data.get('title', '')[:60] or url}",
                    detail=f"{len(text.split())} words extracted",
                    status="done",
                )
                rel = await _capture_panel_screenshot(page, task_id, "page-load")
                if rel:
                    await emit_screenshot(task_id, rel, caption=data.get("title", "")[:80])
            return PageContent(
                title=data.get("title", ""),
                url=url,
                text_content=text,
                word_count=len(text.split()),
            )

            # Let user see the page for a moment
            await asyncio.sleep(3)
        except Exception as e:
            log.warning(f"Visit failed for '{url}': {e}")
            return PageContent(
                title="Error",
                url=url,
                text_content=f"Failed to load page: {e}",
                word_count=0,
            )
        # Don't close — keep pages visible

    # -- Screenshot ------------------------------------------------------------

    async def screenshot(self, url: str, path: str = None, task_id: str | None = None) -> str:
        """Take screenshot of a page. Returns file path to PNG."""
        page = await self._new_page()

        try:
            if task_id:
                await emit_browser_action(task_id, "Screenshot", url=url)
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(1000)  # let rendering settle

            if not path:
                tmp = tempfile.mktemp(suffix=".png", prefix="jarvis_screenshot_")
                path = tmp

            await page.screenshot(path=path, full_page=True)
            log.info(f"Screenshot saved: {path}")
            if task_id:
                # Also save to the panel-served dir so the frontend can show it.
                rel = await _capture_panel_screenshot(page, task_id, "explicit")
                if rel:
                    await emit_screenshot(task_id, rel, caption=url)
            return path

        except Exception as e:
            log.warning(f"Screenshot failed for '{url}': {e}")
            return ""
        finally:
            await page.close()

    # -- Research (multi-step) -------------------------------------------------

    async def research(self, topic: str, task_id: str | None = None) -> ResearchResult:
        """Multi-step research: search -> visit top results -> compile findings."""
        results = await self.search(topic, task_id=task_id)
        sources = []
        contents = []

        for r in results[:3]:
            try:
                page_content = await self.visit(r.url, task_id=task_id)
                sources.append(r.url)
                contents.append(
                    f"## {r.title}\nURL: {r.url}\n\n{page_content.text_content[:1500]}"
                )
            except Exception:
                continue

        summary = "\n\n---\n\n".join(contents) if contents else "No results found."
        if task_id:
            await emit_step(
                task_id,
                f"Synthesized {len(sources)} sources",
                detail=topic[:120],
                status="done",
            )

        return ResearchResult(
            topic=topic,
            sources=sources,
            summary=summary,
            key_findings=[r.title for r in results[:3]],
        )

    # -- Lifecycle -------------------------------------------------------------

    async def close(self):
        """Shut down the browser."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
            log.info("Browser closed")
        except Exception as e:
            log.warning(f"Browser close error: {e}")
        finally:
            self._pw = None
            self._browser = None
            self._context = None
