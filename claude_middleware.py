"""
VALET Claude Code middleware — Haiku-based structured-result extraction.

After a WorkSession turn completes, this module takes the assistant's final
response + any tool-result snippets collected during streaming and asks Haiku
to extract structured "cards" (web links, products, locations, images).

Output cards are emitted as `result.<kind>` panel events so the Process Panel
can render them as discrete components alongside the existing markdown text.

Failure modes are tolerant by design:
  - Haiku call fails → no cards emitted, markdown still renders, error logged.
  - JSON parse fails → no cards emitted, markdown still renders, error logged.
  - Schema validation fails → drop the invalid card, keep valid ones.

This is always belt-and-suspenders: the markdown text is the source of truth;
cards are a secondary, opportunistic UI layer.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from process_events import bus, Event

log = logging.getLogger("valet.claude_middleware")


# Currency normalization — the user is in the US; any non-USD price that
# slips through from a foreign-locale source page gets stripped (with a
# loud warning to valet.err.log) rather than displayed. The system
# prompt also instructs Opus to omit non-USD prices, but Haiku has been
# observed passing them through verbatim from the model's source text.
_NON_USD_SYMBOLS = ("£", "€", "¥", "₹", "₩", "₽", "₪", "₺", "฿")
_NON_USD_CODES_RE = re.compile(
    r"\b(GBP|EUR|JPY|CAD|AUD|INR|KRW|CNY|RMB|CHF|SEK|NOK|DKK|MXN|BRL|RUB|TRY)\b",
    re.IGNORECASE,
)


def _strip_non_usd_price(price: Optional[str]) -> tuple[Optional[str], bool]:
    """Detect non-USD currency in an extracted price string.

    Returns (clean_price, was_stripped). If a non-USD symbol or three-letter
    currency code is present, returns (None, True). Otherwise returns
    (price unchanged, False).
    """
    if not price:
        return price, False
    for sym in _NON_USD_SYMBOLS:
        if sym in price:
            return None, True
    if _NON_USD_CODES_RE.search(price):
        return None, True
    return price, False


# ---------------------------------------------------------------------------
# Pydantic schema — strict enough to catch garbage, loose enough that any
# missing optional field doesn't disqualify the card.
# ---------------------------------------------------------------------------

CardType = Literal["web", "product", "location", "image"]


class ResultCard(BaseModel):
    type: CardType
    title: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(..., min_length=1, max_length=600)
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[str] = None      # display string ("$45", "$8/lb")
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class CardSet(BaseModel):
    cards: list[ResultCard] = []


# ---------------------------------------------------------------------------
# Haiku extraction prompt (lives in code so it can evolve without a schema
# bump; cheap-to-edit since Haiku is forgiving of prompt churn).
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM = """You extract structured "result cards" from a Claude Code response.

The user asked a research-style question and Claude Code answered with prose
that may mention products, web pages, locations, or images. Your job is to
detect those and emit them as cards so the UI can render them as discrete
components.

Card types:
- web:      a webpage being summarized or cited as a source.
- product:  a buyable item (has a name, usually a price).
- location: a place (has a name, usually an address).
- image:    an image being referenced by URL.

ONLY emit a card when the response clearly mentions a distinct item. Don't
fabricate fields. Missing fields are fine — omit them rather than guessing.
Don't emit a "web" card for every URL — only when the URL represents a
source/citation worth its own card. Casual mentions of well-known sites
(e.g. "googled it") don't count.

Output ONLY a JSON object with this shape, nothing else:

{
  "cards": [
    {
      "type": "web" | "product" | "location" | "image",
      "title": "...",
      "summary": "1-2 sentence summary",
      "source_url": "...optional",
      "image_url": "...optional",
      "price": "...optional",
      "address": "...optional",
      "lat": 0.0,
      "lng": 0.0
    }
  ]
}

If nothing extractable, return {"cards": []}. Be conservative.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_and_emit(
    *,
    response_text: str,
    tool_result_snippets: list[str],
    task_id: str,
    anthropic_client: Any,
) -> int:
    """Run extraction on a completed WorkSession turn; emit result.* events.

    `response_text` — the assistant's final reply (concatenated text blocks).
    `tool_result_snippets` — short snippets from tool_result blocks for
        additional context (e.g. WebFetch returned URL summaries Haiku
        wouldn't otherwise see in just the assistant text).
    `task_id` — for grouping in the panel.
    `anthropic_client` — async Anthropic client.

    Returns count of cards emitted. Never raises.
    """
    if not anthropic_client:
        return 0
    if not response_text or len(response_text.strip()) < 20:
        # Nothing meaningful to extract from.
        return 0

    # Bound the input. Haiku has plenty of context but we don't need novels.
    combined = response_text[:8000]
    if tool_result_snippets:
        joined_tools = "\n\n--- TOOL RESULTS ---\n" + "\n---\n".join(
            s[:2000] for s in tool_result_snippets[:8]
        )
        combined = combined + joined_tools[:6000]

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": combined}],
        )
        raw = response.content[0].text.strip()
    except Exception as e:
        log.warning(f"middleware: Haiku call failed: {e}")
        return 0

    # Strip code-fence wrappers if present (Haiku sometimes adds them despite
    # the "JSON only" instruction).
    if raw.startswith("```"):
        raw = raw.strip("`")
        # drop leading "json" line
        if raw.lower().startswith("json\n"):
            raw = raw[5:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except Exception as e:
        log.info(f"middleware: JSON parse failed ({e}); falling back to text only")
        return 0

    # Schema validation — drop invalid cards individually, keep the good ones.
    try:
        cardset = CardSet.model_validate(parsed)
    except ValidationError as ve:
        log.info(f"middleware: schema validation failed at top level ({ve.error_count()} errors); trying per-card")
        good: list[ResultCard] = []
        for raw_card in (parsed.get("cards") or []):
            try:
                good.append(ResultCard.model_validate(raw_card))
            except ValidationError:
                continue
        cardset = CardSet(cards=good)

    if not cardset.cards:
        return 0

    # Currency normalization — strip non-USD prices that slipped through
    # despite the system-prompt locale anchor. Loudly log so we can audit
    # how often Opus / Haiku miss the instruction.
    for card in cardset.cards:
        clean_price, was_stripped = _strip_non_usd_price(card.price)
        if was_stripped:
            log.warning(
                "currency_mismatch: dropping non-USD price %r from card %r (source=%r)",
                card.price, card.title, card.source_url,
            )
            card.price = clean_price

    # Image enrichment — for product cards with a source_url but no model-
    # supplied image_url, fetch the source page and look for og:image /
    # twitter:image. Source-attributed only: a card is more trustworthy when
    # the image actually comes from where the product info came from, so we
    # deliberately don't fall back to generic image search.
    #
    # Runs all candidates in parallel under a single asyncio.gather, so
    # total added latency is the slowest single fetch (capped at 1.5s by
    # fetch_page_preview), not the sum.
    await _enrich_card_images(cardset.cards)

    for card in cardset.cards:
        event_type = f"result.{card.type}"
        await bus.emit(Event(
            type=event_type,
            task_id=task_id,
            title=card.title,
            detail=card.summary,
            status="done",
            payload={
                "type": card.type,
                "title": card.title,
                "summary": card.summary,
                "source_url": card.source_url,
                "image_url": card.image_url,
                "price": card.price,
                "address": card.address,
                "lat": card.lat,
                "lng": card.lng,
            },
        ))

    log.info(f"middleware: emitted {len(cardset.cards)} result card(s) for task {task_id}")
    return len(cardset.cards)


async def _enrich_card_images(cards: list["ResultCard"]) -> None:
    """Fill in `image_url` from og:image / twitter:image on cards that have
    a source URL but no image. Product cards only — for now.

    Mutates `cards` in place; never raises (per-card failures are silent).
    """
    # Import here so the dependency stays local and the module is still
    # easy to use in test environments that stub out the helper.
    import asyncio
    from page_preview import fetch_page_preview

    candidates = [
        c for c in cards
        if c.type == "product" and c.source_url and not c.image_url
    ]
    if not candidates:
        return

    async def _one(c: "ResultCard") -> None:
        try:
            preview = await fetch_page_preview(c.source_url, timeout=1.5)
            img = preview.get("og_image_url")
            if img:
                c.image_url = img
        except Exception as e:
            log.debug(f"middleware: image enrichment failed for {c.source_url}: {e}")

    await asyncio.gather(*[_one(c) for c in candidates], return_exceptions=True)
