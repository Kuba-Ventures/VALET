"""
Native weather lookup for JARVIS.

Replaces the generic [ACTION:RESEARCH] WebFetch path with a dedicated,
fast Open-Meteo pipeline:

  geocode(name) → {lat, lon, timezone}
  fetch_forecast(lat, lon, tz) → current conditions + 7-day daily slabs
  synthesize_alert(...) → severe / UV / heavy-rain banner string + level
  format_voice_summary(...) → JARVIS-style 1-2 sentence butler line
  build_card_payload(...) → shape for the floating result.weather card

Open-Meteo is free, no API key, and was already in use elsewhere in the
codebase (server.py background thread). One worldwide source covers both
geocoding and forecast endpoints. WMO weather codes (returned by the
forecast endpoint) are mapped to human-readable labels + emoji here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import httpx

log = logging.getLogger("jarvis.weather")


# ---------------------------------------------------------------------------
# WMO weather code maps
# ---------------------------------------------------------------------------
# Open-Meteo returns the standard WMO 4677 code set. Maps cover the cases
# that actually appear in 7-day forecasts; anything missing falls back to a
# generic label / cloud emoji.

WEATHER_CODE_MAP: dict[int, str] = {
    0:  "Clear",
    1:  "Mainly clear",
    2:  "Partly cloudy",
    3:  "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Heavy rain showers",
    82: "Violent rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}

WEATHER_CODE_EMOJI: dict[int, str] = {
    0: "☀️",  1: "🌤️",  2: "⛅",  3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️", 56: "🌧️", 57: "🌧️",
    61: "🌦️", 63: "🌧️", 65: "🌧️", 66: "🌧️", 67: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "❄️", 77: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "⛈️",
    85: "🌨️", 86: "❄️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}

# Codes that warrant a "severe weather" banner regardless of UV / precip
# probability. Heavy rain / heavy snow / thunderstorms / violent showers.
SEVERE_CODES: set[int] = {65, 67, 75, 82, 86, 95, 96, 99}


def code_label(code: int | None) -> str:
    if code is None:
        return ""
    return WEATHER_CODE_MAP.get(code, "Cloudy")


def code_emoji(code: int | None) -> str:
    if code is None:
        return "☁️"
    return WEATHER_CODE_EMOJI.get(code, "☁️")


# ---------------------------------------------------------------------------
# HTTP — geocoding + forecast
# ---------------------------------------------------------------------------

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


async def _geocode_one(q: str) -> Optional[dict]:
    """Single HTTP call to Open-Meteo's geocoder. None on miss."""
    params = {"name": q, "count": 1, "language": "en", "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.get(GEOCODE_URL, params=params)
        if r.status_code != 200:
            log.warning("geocode: %s → HTTP %s", q, r.status_code)
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        return {
            "name": top.get("name") or q,
            "admin1": top.get("admin1") or "",
            "country": top.get("country") or "",
            "lat": float(top["latitude"]),
            "lon": float(top["longitude"]),
            "timezone": top.get("timezone") or "auto",
        }
    except Exception as e:
        log.warning(f"geocode {q!r} failed: {e}")
        return None


async def geocode(query: str) -> Optional[dict]:
    """Resolve a free-form location string to {name, country, lat, lon, timezone}.

    Open-Meteo's geocoder only accepts a name parameter and is picky about
    extra tokens ("Brooklyn NY" and "St. Petersburg, FL" both miss; "Brooklyn"
    and "St. Petersburg" hit). Try the query as-given, then progressively
    strip junk to give common formats a chance:

      1. Verbatim
      2. First comma-separated segment ("St. Petersburg, FL" → "St. Petersburg")
      3. First whitespace token ("Brooklyn NY" → "Brooklyn")

    Returns the first hit, or None if all attempts miss. The user can throw
    a city + state, a country, a postal code, or a full address; we degrade
    gracefully through the variants.
    """
    q = (query or "").strip()
    if not q:
        return None
    tried: set[str] = set()
    variants = [q]
    if "," in q:
        variants.append(q.split(",", 1)[0].strip())
    parts = q.split()
    if len(parts) > 1:
        variants.append(parts[0])
    for v in variants:
        v = v.strip()
        if not v or v in tried:
            continue
        tried.add(v)
        hit = await _geocode_one(v)
        if hit:
            return hit
    return None


async def fetch_forecast(lat: float, lon: float, timezone: str = "auto") -> Optional[dict]:
    """Pull current conditions + 7-day daily forecast from Open-Meteo.

    Single HTTP call. Returns the raw JSON (with `current` and `daily` blocks)
    or None on failure. Temperature unit is Fahrenheit, wind in mph — matches
    JARVIS's existing US-centric voice copy. Easy to flip via params later.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone or "auto",
        "current": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m",
            "wind_speed_10m", "weather_code",
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "uv_index_max", "precipitation_probability_max",
            "sunrise", "sunset",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "forecast_days": 7,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(FORECAST_URL, params=params)
        if r.status_code != 200:
            log.warning("forecast: %s,%s → HTTP %s", lat, lon, r.status_code)
            return None
        return r.json()
    except Exception as e:
        log.warning(f"fetch_forecast {lat},{lon} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Alert synthesis
# ---------------------------------------------------------------------------

def synthesize_alert(daily: dict, current: dict) -> Optional[dict]:
    """Highest-priority alert if any. Severe > UV > heavy rain probability.

    Open-Meteo doesn't ship NWS-style bulletins; we derive a banner from the
    forecast slabs themselves. Returns:
      {"level": "severe"|"uv"|"rain", "text": "..."} | None
    """
    current_code = current.get("weather_code")
    if isinstance(current_code, int) and current_code in SEVERE_CODES:
        return {
            "level": "severe",
            "text": f"{code_label(current_code)} now.",
        }
    daily_codes = daily.get("weather_code") or []
    for idx, code in enumerate(daily_codes[:3]):  # next 3 days
        if isinstance(code, int) and code in SEVERE_CODES:
            when = "today" if idx == 0 else ("tomorrow" if idx == 1 else "in two days")
            return {
                "level": "severe",
                "text": f"{code_label(code)} expected {when}.",
            }

    uv_max_list = daily.get("uv_index_max") or []
    uv_today = uv_max_list[0] if uv_max_list else None
    if isinstance(uv_today, (int, float)) and uv_today >= 8:
        return {
            "level": "uv",
            "text": f"UV index hits {round(uv_today)} today. Hat advised.",
        }

    precip_list = daily.get("precipitation_probability_max") or []
    precip_today = precip_list[0] if precip_list else None
    if isinstance(precip_today, (int, float)) and precip_today >= 70:
        return {
            "level": "rain",
            "text": f"{int(precip_today)}% chance of rain today. Umbrella weather.",
        }

    return None


# ---------------------------------------------------------------------------
# Voice + card formatting
# ---------------------------------------------------------------------------

def format_voice_summary(
    forecast: dict,
    location: str,
    alert: Optional[dict],
    when: str = "today",
) -> str:
    """1-2 sentence butler line for TTS + the on-screen caption.

    `when` selects the time scope the user asked about:
      "today"     → live conditions now + today's high (default)
      "tomorrow"  → tomorrow's high/low + condition (daily index 1)
      "day_after" → the day after tomorrow (daily index 2)
      "week"      → range of highs across the 7-day outlook
    A future day has no "current temperature", so those lines lead with the
    high/low slab instead of the live reading.
    """
    current = (forecast or {}).get("current") or {}
    daily = (forecast or {}).get("daily") or {}
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []

    def _fmt(t: Any) -> str:
        try:
            return f"{int(round(float(t)))}"
        except Exception:
            return "?"

    def _alert_suffix() -> str:
        return f" {alert['text']}" if alert and alert.get("text") else ""

    # ── Future single day (tomorrow / day after) ──────────────────────────
    day_index = {"tomorrow": 1, "day_after": 2}.get(when)
    if day_index is not None and day_index < len(highs):
        when_word = "Tomorrow" if day_index == 1 else "The day after tomorrow"
        label = code_label(codes[day_index] if day_index < len(codes) else None).lower()
        high = highs[day_index]
        low = lows[day_index] if day_index < len(lows) else None
        bits = [f"{when_word} in {location}, sir"]
        if label and label != "cloudy":
            bits.append(label)
        line = ", ".join(bits) + "."
        if high is not None and low is not None:
            line += f" High of {_fmt(high)}, low of {_fmt(low)}."
        elif high is not None:
            line += f" High of {_fmt(high)}."
        return line + _alert_suffix()

    # ── Week-ahead range ──────────────────────────────────────────────────
    if when == "week":
        week_highs = [h for h in highs[:7] if isinstance(h, (int, float))]
        if week_highs:
            lo, hi = min(week_highs), max(week_highs)
            span = f"{_fmt(lo)} to {_fmt(hi)}" if lo != hi else _fmt(hi)
            return f"This week in {location}, sir, highs from {span}.{_alert_suffix()}"
        # fall through to today if no daily data

    # ── Today (default) — live conditions ─────────────────────────────────
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    high_today = highs[0] if highs else None
    label = code_label(code).lower()
    intro_bits = []
    if temp is not None:
        intro_bits.append(f"{_fmt(temp)} in {location}, sir")
    else:
        intro_bits.append(f"In {location}, sir")
    if label and label != "cloudy":
        intro_bits.append(label)
    intro = ", ".join(intro_bits) + "."

    if alert and alert.get("text"):
        return f"{intro} {alert['text']}"
    if high_today is not None:
        return f"{intro} High of {_fmt(high_today)} today."
    return intro


def _day_name(date_str: str) -> str:
    try:
        return datetime.fromisoformat(date_str).strftime("%a")
    except Exception:
        return date_str


def build_card_payload(forecast: dict, location: str) -> dict:
    """Compact, frontend-ready shape for the result.weather floating card."""
    current = (forecast or {}).get("current") or {}
    daily = (forecast or {}).get("daily") or {}
    alert = synthesize_alert(daily, current)

    days = []
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    uvs = daily.get("uv_index_max") or []
    precips = daily.get("precipitation_probability_max") or []
    for i, date in enumerate(dates[:7]):
        code = codes[i] if i < len(codes) else None
        days.append({
            "date": date,
            "day_name": _day_name(date),
            "high": highs[i] if i < len(highs) else None,
            "low":  lows[i] if i < len(lows) else None,
            "code_label": code_label(code),
            "code_emoji": code_emoji(code),
            "uv_max": uvs[i] if i < len(uvs) else None,
            "precip_pct": precips[i] if i < len(precips) else None,
        })

    sunrises = daily.get("sunrise") or [""]
    sunsets = daily.get("sunset") or [""]

    return {
        "location": location,
        "current": {
            "temp_f": current.get("temperature_2m"),
            "feels_like_f": current.get("apparent_temperature"),
            "code_label": code_label(current.get("weather_code")),
            "code_emoji": code_emoji(current.get("weather_code")),
            "humidity": current.get("relative_humidity_2m"),
            "wind_mph": current.get("wind_speed_10m"),
        },
        "alert": alert,
        "daily": days,
        "sunrise": sunrises[0] if sunrises else "",
        "sunset":  sunsets[0] if sunsets else "",
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def quick_summary_for_prompt(forecast: dict, location: str) -> str:
    """One-line string the dynamic system prompt's `weather_info` slot expects.

    The background context thread (server.py) used to write this directly to
    `_ctx_cache["weather"]`. Now we also write the rich dict via
    build_card_payload, but Haiku still wants a single line — this is it.
    """
    current = (forecast or {}).get("current") or {}
    daily = (forecast or {}).get("daily") or {}
    temp = current.get("temperature_2m")
    label = code_label(current.get("weather_code"))
    highs = daily.get("temperature_2m_max") or []
    high = highs[0] if highs else None
    def _f(t):
        try:
            return f"{int(round(float(t)))}°F"
        except Exception:
            return "?"
    parts = [location]
    if temp is not None:
        parts.append(f"{_f(temp)} {label.lower()}")
    if high is not None:
        parts.append(f"high {_f(high)}")
    return ", ".join(p for p in parts if p)
