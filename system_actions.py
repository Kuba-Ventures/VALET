"""Small system-action set for the voice console (Stage 2).

"lock the screen", "turn up the volume", "empty the trash" resolve to a concrete
command with a SAFETY TIER — no LLM. ``match_action`` is pure and unit-tested;
the caller runs ``spec.argv`` (always list-form, never a shell string) and is
responsible for gating: Tier 0 runs immediately, Tier 1 (destructive) goes
through the existing confirm card + kill switch first.

Brightness is intentionally omitted — there's no reliable built-in CLI for it.
"""

from __future__ import annotations

from dataclasses import dataclass

TIER_SAFE = 0          # run immediately
TIER_DESTRUCTIVE = 1   # confirm-gated (data loss / interrupts the session)

_LOCK_CMD = (
    "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/"
    "CGSession",
    "-suspend",
)


def _osa(script: str) -> tuple[str, ...]:
    return ("osascript", "-e", script)


@dataclass(frozen=True)
class ActionSpec:
    name: str               # stable id (matches the skill name)
    label: str              # spoken back ("Locking the screen")
    argv: tuple[str, ...]   # list-form command, run with create_subprocess_exec
    tier: int               # TIER_SAFE | TIER_DESTRUCTIVE


_ACTIONS: dict[str, ActionSpec] = {
    "lock_screen": ActionSpec("lock_screen", "Locking the screen", _LOCK_CMD, TIER_SAFE),
    "display_sleep": ActionSpec(
        "display_sleep", "Turning off the display", ("pmset", "displaysleepnow"), TIER_SAFE),
    "sleep": ActionSpec(
        "sleep", "Going to sleep", ("pmset", "sleepnow"), TIER_SAFE),
    "screensaver": ActionSpec(
        "screensaver", "Starting the screensaver",
        _osa('tell application "System Events" to start current screen saver'), TIER_SAFE),
    "volume_up": ActionSpec(
        "volume_up", "Turning it up",
        _osa("set volume output volume (output volume of (get volume settings) + 12)"),
        TIER_SAFE),
    "volume_down": ActionSpec(
        "volume_down", "Turning it down",
        _osa("set volume output volume (output volume of (get volume settings) - 12)"),
        TIER_SAFE),
    "mute": ActionSpec(
        "mute", "Muting", _osa("set volume with output muted"), TIER_SAFE),
    "unmute": ActionSpec(
        "unmute", "Unmuting", _osa("set volume without output muted"), TIER_SAFE),
    "empty_trash": ActionSpec(
        "empty_trash", "Emptying the trash",
        _osa('tell application "Finder" to empty trash'), TIER_DESTRUCTIVE),
    "restart": ActionSpec(
        "restart", "Restarting",
        _osa('tell application "System Events" to restart'), TIER_DESTRUCTIVE),
    "shutdown": ActionSpec(
        "shutdown", "Shutting down",
        _osa('tell application "System Events" to shut down'), TIER_DESTRUCTIVE),
}

# Spoken trigger phrases → action name. Checked as whole-word phrases (see
# ``match_action``). Order matters: more specific phrases first so "sleep the
# display" doesn't get swallowed by "sleep".
_TRIGGERS: list[tuple[str, str]] = [
    ("lock the screen", "lock_screen"),
    ("lock screen", "lock_screen"),
    ("lock the mac", "lock_screen"),
    ("lock my mac", "lock_screen"),
    ("lock my computer", "lock_screen"),
    ("lock the computer", "lock_screen"),
    ("sleep the display", "display_sleep"),
    ("sleep the screen", "display_sleep"),
    ("turn off the display", "display_sleep"),
    ("turn off the screen", "display_sleep"),
    ("screen off", "display_sleep"),
    ("go to sleep", "sleep"),
    ("sleep the mac", "sleep"),
    ("sleep my mac", "sleep"),
    ("sleep the computer", "sleep"),
    ("put the mac to sleep", "sleep"),
    ("put my computer to sleep", "sleep"),
    ("start the screensaver", "screensaver"),
    ("start screensaver", "screensaver"),
    ("screensaver", "screensaver"),
    ("screen saver", "screensaver"),
    ("turn up the volume", "volume_up"),
    ("volume up", "volume_up"),
    ("raise the volume", "volume_up"),
    ("increase the volume", "volume_up"),
    ("louder", "volume_up"),
    ("turn down the volume", "volume_down"),
    ("volume down", "volume_down"),
    ("lower the volume", "volume_down"),
    ("decrease the volume", "volume_down"),
    ("quieter", "volume_down"),
    ("unmute", "unmute"),
    ("mute", "mute"),
    ("silence", "mute"),
    ("empty the trash", "empty_trash"),
    ("empty trash", "empty_trash"),
    ("take out the trash", "empty_trash"),
    ("empty the bin", "empty_trash"),
    ("restart the mac", "restart"),
    ("restart my mac", "restart"),
    ("restart the computer", "restart"),
    ("restart my computer", "restart"),
    ("reboot", "restart"),
    ("shut down", "shutdown"),
    ("shutdown", "shutdown"),
    ("turn off the mac", "shutdown"),
    ("turn off my mac", "shutdown"),
    ("power off", "shutdown"),
]


def get(name: str) -> ActionSpec | None:
    return _ACTIONS.get(name)


def match_action(spoken: str) -> ActionSpec | None:
    """Best system action for a spoken phrase, or None (caller falls through).
    Triggers match as whole-word phrases so "mute" won't fire inside another word."""
    s = (spoken or "").strip().lower().rstrip(" .?!")
    if not s:
        return None
    padded = f" {s} "
    for phrase, name in _TRIGGERS:
        if f" {phrase} " in padded:
            return _ACTIONS[name]
    return None
