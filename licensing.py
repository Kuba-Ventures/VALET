"""License validation against the hosted proxy/site.

The desktop app ships with no vendor secrets. It carries a LICENSE_KEY which it
validates against `{PROXY_BASE_URL}/api/license/validate` on startup and
periodically. A short offline grace window lets the app keep working through
transient network failures, but a key that validates (online) as canceled,
past_due, or invalid disables the assistant loop.

State (last status + last-OK timestamp) is persisted under data/ so the grace
window survives restarts.
"""

import json
import logging
import os
import time
from typing import Optional

import httpx

log = logging.getLogger("valet.licensing")

# Statuses that grant access (must match the proxy / Phase-1 vocabulary).
ENTITLED = {"active", "trialing"}

# How long the app keeps working offline after a successful validation.
GRACE_SECONDS = 7 * 86400  # 7 days

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "license_state.json"
)

_state = {"status": "unknown", "checked_at": 0.0, "last_ok_at": 0.0}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    try:
        with open(_STATE_FILE) as f:
            data = json.load(f)
        for k in _state:
            if k in data:
                _state[k] = data[k]
    except Exception:
        pass
    _loaded = True


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump(_state, f)
    except Exception as e:
        log.debug(f"license state save failed: {e}")


async def validate(license_key: str, proxy_base_url: str) -> dict:
    """Validate online. On network failure, mark 'offline' and lean on the grace
    window (is_entitled handles the decision). Returns a copy of the state."""
    _load()
    now = time.time()

    if not license_key:
        _state.update({"status": "invalid", "checked_at": now})
        _save()
        return dict(_state)

    url = f"{proxy_base_url.rstrip('/')}/api/license/validate"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(url, json={"license_key": license_key})
            data = r.json()
        status = data.get("status", "invalid")
        _state.update({"status": status, "checked_at": now})
        if status in ENTITLED:
            _state["last_ok_at"] = now
        _save()
        log.info(f"license validated: status={status}")
        return dict(_state)
    except Exception as e:
        # Network/transport failure — do not overwrite a known-bad status with a
        # definitive one; mark offline and defer to the grace window.
        log.warning(f"license validation offline: {e}")
        _state.update({"status": "offline", "checked_at": now})
        _save()
        return dict(_state)


def is_entitled() -> bool:
    """Current entitlement decision, including the offline grace window."""
    _load()
    status = _state.get("status", "unknown")
    if status in ENTITLED:
        return True
    if status in ("offline", "unknown"):
        last_ok = _state.get("last_ok_at", 0.0)
        return bool(last_ok and (time.time() - last_ok) < GRACE_SECONDS)
    # canceled / past_due / invalid — definitively not entitled.
    return False


def status_label() -> str:
    """Human-facing label for the settings panel."""
    _load()
    status = _state.get("status", "unknown")
    if status in ("offline", "unknown") and is_entitled():
        return "offline (grace period)"
    return status
