"""System-settings deep-link map + matcher for the voice console (Stage 2).

"go to Bluetooth settings" / "open display settings" resolve to an
``x-apple.systempreferences:`` URL and open the pane directly — no LLM. Mirrors
``app_index.match_app``: ``match_setting`` is pure and unit-tested; only the
caller touches the system (``open <url>``).

Pane IDs are the macOS Ventura+ "…-Settings.extension" identifiers (current
System Settings). If a pane id is stale on a given OS, ``open`` still launches
System Settings, so the command degrades to "opened settings" rather than failing
hard. The privacy panes reuse the same ids the onboarding flow already ships.
"""

from __future__ import annotations

import difflib

# label → x-apple.systempreferences: URL. The label is what Vee speaks back.
_PANES: dict[str, str] = {
    "Bluetooth": "x-apple.systempreferences:com.apple.BluetoothSettings",
    "Wi-Fi": "x-apple.systempreferences:com.apple.wifi-settings-extension",
    "Network": "x-apple.systempreferences:com.apple.Network-Settings.extension",
    "Displays": "x-apple.systempreferences:com.apple.Displays-Settings.extension",
    "Sound": "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "Notifications": "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
    "Battery": "x-apple.systempreferences:com.apple.Battery-Settings.extension",
    "Keyboard": "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
    "Trackpad": "x-apple.systempreferences:com.apple.Trackpad-Settings.extension",
    "Mouse": "x-apple.systempreferences:com.apple.Mouse-Settings.extension",
    "General": "x-apple.systempreferences:com.apple.systempreferences.GeneralSettings",
    "Appearance": "x-apple.systempreferences:com.apple.Appearance-Settings.extension",
    "Wallpaper": "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension",
    "Accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "Microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "Screen Recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "Input Monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "Privacy & Security": "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
}

# Spoken aliases → canonical label above. Small, high-value; the fuzzy matcher
# handles the long tail and STT slips.
_ALIASES: dict[str, str] = {
    "wifi": "Wi-Fi",
    "wi fi": "Wi-Fi",
    "wireless": "Wi-Fi",
    "internet": "Network",
    "display": "Displays",
    "monitor": "Displays",
    "monitors": "Displays",
    "brightness": "Displays",
    "volume": "Sound",
    "audio": "Sound",
    "speakers": "Sound",
    "notification": "Notifications",
    "power": "Battery",
    "energy": "Battery",
    "mouse": "Mouse",
    "trackpad": "Trackpad",
    "dark mode": "Appearance",
    "light mode": "Appearance",
    "theme": "Appearance",
    "wallpaper": "Wallpaper",
    "desktop picture": "Wallpaper",
    "background": "Wallpaper",
    "mic": "Microphone",
    "screen recording": "Screen Recording",
    "input monitoring": "Input Monitoring",
    "privacy": "Privacy & Security",
    "security": "Privacy & Security",
}

# Words stripped from a spoken target so "the bluetooth settings" → "bluetooth".
_NOISE = ("system ", "the ", "my ")
_SUFFIXES = (" settings", " preferences", " setting", " pane", " panel", " page")


def _normalize(spoken: str) -> str:
    s = (spoken or "").strip().lower()
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
    changed = True
    while changed:
        changed = False
        for pre in _NOISE:
            if s.startswith(pre):
                s = s[len(pre):]
                changed = True
    return s.strip()


def match_setting(spoken: str) -> tuple[str, str] | None:
    """Best (label, url) for a spoken settings target, or None if no confident
    match (caller then falls through to the LLM)."""
    s = _normalize(spoken)
    if not s:
        return None
    if s in _ALIASES:
        label = _ALIASES[s]
        return (label, _PANES[label])
    lower = {label.lower(): label for label in _PANES}
    if s in lower:                                    # exact label
        label = lower[s]
        return (label, _PANES[label])
    subs = [label for label in _PANES if s in label.lower()]
    if subs:                                          # substring → shortest
        label = min(subs, key=len)
        return (label, _PANES[label])
    # fuzzy over labels + aliases (STT slips like "blue tooth")
    keys = list(lower.keys()) + list(_ALIASES.keys())
    close = difflib.get_close_matches(s, keys, n=1, cutoff=0.84)
    if close:
        hit = close[0]
        label = _ALIASES.get(hit) or lower.get(hit)
        if label:
            return (label, _PANES[label])
    return None
