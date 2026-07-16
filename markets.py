"""Live market quotes — stocks, indices, crypto, FX, commodities — via Yahoo
Finance's keyless endpoints. Mirrors weather.py / sports.py: async httpx, every
external failure caught and turned into None, a build_card_payload() the frontend
renders plus a format_summary() the butler speaks.

Resolution is dynamic: a small alias table catches the common phrasings
instantly (no network), and anything else resolves through Yahoo's symbol
search, so "price of Palantir" or a bare ticker works without a hardcoded list.
"""

from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger("valet.markets")

_QUOTE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_TIMEOUT = 8.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Common phrases → symbol. Instant (no network) and doubles as the fast-path
# gate. Longest key wins ("s&p 500" before "s&p").
_ALIASES: dict[str, str] = {
    "s&p 500": "^GSPC", "s&p500": "^GSPC", "s and p": "^GSPC", "s&p": "^GSPC",
    "sp500": "^GSPC", "the market": "^GSPC",
    "dow jones": "^DJI", "dow": "^DJI",
    "nasdaq": "^IXIC", "russell 2000": "^RUT", "russell": "^RUT", "vix": "^VIX",
    "ftse": "^FTSE", "nikkei": "^N225",
    "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "dogecoin": "DOGE-USD", "doge": "DOGE-USD", "solana": "SOL-USD",
    "gold": "GC=F", "silver": "SI=F", "crude oil": "CL=F", "oil": "CL=F",
    "apple": "AAPL", "tesla": "TSLA", "microsoft": "MSFT", "amazon": "AMZN",
    "google": "GOOGL", "alphabet": "GOOGL", "meta": "META", "facebook": "META",
    "nvidia": "NVDA", "netflix": "NFLX", "palantir": "PLTR", "coinbase": "COIN",
}

# Words that signal a markets query (used for the server's sync fast-path gate).
MARKET_CUES = (
    "stock price", "share price", "stock", "shares", "how's the", "hows the",
    "price of", "market", "nasdaq", "dow", "s&p", "trading at", "ticker",
    "crypto", "bitcoin", "ethereum", "index", "how is the", "quote",
)


def resolve_symbol_sync(query: str) -> str | None:
    """Instant alias / ticker resolution (no network). Returns a Yahoo symbol or
    None. Used both as the fast-path gate and the happy-path resolver."""
    q = (query or "").lower()
    for kw in sorted(_ALIASES, key=len, reverse=True):
        if kw in q:
            return _ALIASES[kw]
    # "$AAPL" or a bare all-caps ticker token
    m = re.search(r"\$([A-Za-z]{1,5})\b", query or "")
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([A-Z]{2,5})\b", query or "")
    if m and m.group(1).lower() not in {"the", "how", "what", "usd"}:
        return m.group(1).upper()
    return None


_SEARCH_STOP = {
    "what", "whats", "what's", "is", "the", "price", "of", "how", "hows",
    "how's", "current", "stock", "share", "shares", "quote", "for", "doing",
    "s", "trading", "at", "much", "cost", "worth", "value", "today", "right",
    "now", "a", "market",
}


async def _get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            r = await client.get(url, params=params or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("Yahoo GET failed %s %s: %s", url, params, e)
        return None


async def _search_symbol(query: str) -> str | None:
    """Resolve a company/instrument NAME to a Yahoo symbol via Yahoo search."""
    toks = [t for t in re.split(r"[^a-z0-9&]+", (query or "").lower()) if t and t not in _SEARCH_STOP]
    q = " ".join(toks) if toks else (query or "")
    if not q.strip():
        return None
    data = await _get_json(_SEARCH, {"q": q, "quotesCount": 6, "newsCount": 0})
    if not data:
        return None
    quotes = data.get("quotes") or []
    # Prefer equities/indices/crypto with a symbol; take the first good hit.
    for want in ("EQUITY", "INDEX", "CRYPTOCURRENCY", "ETF", "CURRENCY", "FUTURE"):
        for qd in quotes:
            if qd.get("quoteType") == want and qd.get("symbol"):
                return qd["symbol"]
    return quotes[0].get("symbol") if quotes and quotes[0].get("symbol") else None


# Clean spoken names for aliased symbols (Yahoo's shortName is noisy:
# "Bitcoin USD", "Gold Aug 26", "S&P 500 INDEX").
_DISPLAY = {
    "^GSPC": "S&P 500", "^DJI": "Dow Jones", "^IXIC": "Nasdaq",
    "^RUT": "Russell 2000", "^VIX": "VIX", "^FTSE": "FTSE 100", "^N225": "Nikkei 225",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "DOGE-USD": "Dogecoin",
    "SOL-USD": "Solana", "GC=F": "Gold", "SI=F": "Silver", "CL=F": "Crude Oil",
}


async def _fetch_quote(symbol: str) -> dict | None:
    data = await _get_json(_QUOTE.format(symbol=symbol), {"interval": "1d", "range": "1d"})
    try:
        meta = data["chart"]["result"][0]["meta"]
    except Exception:
        return None
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return None
    change = (price - prev) if (prev is not None) else None
    pct = (change / prev * 100) if (change is not None and prev) else None
    raw_name = meta.get("shortName") or meta.get("longName") or symbol
    # Strip Yahoo noise: trailing " USD" (crypto), " INDEX", futures month tails.
    clean = re.sub(r"\s+(USD|INDEX)$", "", raw_name).strip()
    name = _DISPLAY.get(meta.get("symbol", symbol)) or _DISPLAY.get(symbol) or clean
    return {
        "symbol": meta.get("symbol", symbol),
        "name": name,
        "price": price,
        "prev_close": prev,
        "change": change,
        "change_pct": pct,
        "currency": meta.get("currency") or "USD",
        "type": meta.get("instrumentType") or "",
    }


def _fmt_price(q: dict) -> str:
    p = q["price"]
    cur = q.get("currency", "USD")
    # Indices: no currency symbol, thousands separator. Everything else: $ or code.
    if (q.get("type") or "").upper() == "INDEX":
        return f"{p:,.2f}".rstrip("0").rstrip(".") if p < 100 else f"{p:,.0f}"
    sym = "$" if cur == "USD" else ""
    dec = 2 if p >= 1 else 6
    body = f"{p:,.{dec}f}"
    return f"{sym}{body}" + ("" if cur == "USD" else f" {cur}")


def format_summary(q: dict) -> str:
    """One-line butler quote: '{name} is trading at {price}, up 1.2% today, sir.'"""
    is_index = (q.get("type") or "").upper() == "INDEX"
    lead = f"The {q['name']}" if is_index else q["name"]
    verb = "is at" if is_index else "is trading at"
    price = _fmt_price(q)
    pct = q.get("change_pct")
    if pct is None:
        return f"{lead} {verb} {price}, sir."
    direction = "up" if pct > 0.05 else ("down" if pct < -0.05 else "flat")
    if direction == "flat":
        return f"{lead} {verb} {price}, flat on the day, sir."
    return f"{lead} {verb} {price}, {direction} {abs(pct):.1f}% today, sir."


def build_card_payload(q: dict) -> dict:
    return {
        "symbol": q["symbol"],
        "name": q["name"],
        "price": _fmt_price(q),
        "change_pct": (round(q["change_pct"], 2) if q.get("change_pct") is not None else None),
        "change": (round(q["change"], 2) if q.get("change") is not None else None),
        "currency": q.get("currency", "USD"),
        "is_index": (q.get("type") or "").upper() == "INDEX",
    }


async def get_markets(query: str) -> dict | None:
    """Top-level: resolve a symbol (alias/ticker, else Yahoo search), fetch the
    quote, and return {payload, summary}. None → caller falls back."""
    symbol = resolve_symbol_sync(query)
    if not symbol:
        symbol = await _search_symbol(query)
    if not symbol:
        return None
    q = await _fetch_quote(symbol)
    if not q:
        # Alias/ticker guess may be wrong — try a name search once.
        alt = await _search_symbol(query)
        if alt and alt != symbol:
            q = await _fetch_quote(alt)
    if not q:
        return None
    return {"payload": build_card_payload(q), "summary": format_summary(q)}
