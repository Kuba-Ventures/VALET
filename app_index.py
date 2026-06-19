"""Installed-app index + fuzzy matcher for the voice console (Stage 1).

The console makes "open X" blazing by skipping the LLM round-trip the old
`[ACTION:OPEN_APP]` path required: it enumerates installed `.app` bundles once
(cached), fuzzy-matches a spoken target to the best app, and launches directly.

Only the scan touches the filesystem; `match_app` is pure and unit-tested with
an injected app list. A confident match is the gate that keeps the fast-path
honest — "open the pod bay doors" finds no app and falls through to the LLM,
so we never hijack a non-app "open X" phrase.
"""

from __future__ import annotations

import difflib
import os
import time
from pathlib import Path
from typing import Callable

_APP_DIRS = (
    "/Applications",
    "/Applications/Utilities",
    "/System/Applications",
    "/System/Applications/Utilities",
    str(Path.home() / "Applications"),
)

# Spoken aliases → the real bundle display name. Small + high-value only; the
# fuzzy matcher handles the long tail. Kept here so STT mishearings and natural
# phrasings ("vs code", "the browser") resolve without a model call.
_ALIASES: dict[str, str] = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "the browser": "Safari",
    "browser": "Safari",
    "system settings": "System Settings",
    "system preferences": "System Settings",
    "settings": "System Settings",
}

# Words we deliberately do NOT fast-path — "terminal" keeps its nuanced
# OPEN_TERMINAL-vs-OPEN_APP routing through the LLM.
_RESERVED = {"terminal"}

_TTL = 300.0
_cache: list[str] = []
_cache_at: float = -1e9


def scan_apps(dirs: tuple[str, ...] | None = None) -> list[str]:
    """Display names (without `.app`) of bundles in the standard locations,
    including one level deep — many apps ship inside a vendor/app subfolder
    (e.g. /Applications/DaVinci Resolve/DaVinci Resolve.app)."""
    names: set[str] = set()
    for d in dirs or _APP_DIRS:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for entry in entries:
            if entry.endswith(".app"):
                names.add(entry[:-4])
                continue
            sub = os.path.join(d, entry)
            if os.path.isdir(sub):
                try:
                    for nested in os.listdir(sub):
                        if nested.endswith(".app"):
                            names.add(nested[:-4])
                except OSError:
                    continue
    return sorted(names)


def installed_apps(clock: Callable[[], float] = time.monotonic) -> list[str]:
    """Cached installed-app list, refreshed every `_TTL` seconds."""
    global _cache, _cache_at
    now = clock()
    if not _cache or now - _cache_at > _TTL:
        _cache = scan_apps()
        _cache_at = now
    return _cache


def match_app(spoken: str, apps: list[str]) -> str | None:
    """Best installed-app name for a spoken target, or None if no confident
    match (the caller then falls through to the LLM)."""
    s = (spoken or "").strip().lower()
    if s.startswith("the "):
        s = s[4:]
    if not s or s in _RESERVED:
        return None
    if s in _ALIASES and _ALIASES[s] in apps:
        return _ALIASES[s]
    lower = {a.lower(): a for a in apps}
    if s in lower:                                   # exact
        return lower[s]
    starts = [a for a in apps if a.lower().startswith(s)]
    if starts:                                       # prefix → shortest/most specific
        return min(starts, key=len)
    subs = [a for a in apps if s in a.lower()]
    if subs:                                         # substring
        return min(subs, key=len)
    sn = s.replace(" ", "")                          # space-insensitive: STT splits
    if sn != s:                                      # "da vinci" → "davinci…"
        despaced = [a for a in apps if sn in a.lower().replace(" ", "")]
        if despaced:
            return min(despaced, key=len)
    close = difflib.get_close_matches(s, list(lower.keys()), n=1, cutoff=0.82)
    if close:                                        # fuzzy (STT slips)
        return lower[close[0]]
    return None
