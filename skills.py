"""Skill registry (Phase K — UC6).

A "skill" is a pre-baked accelerator that short-circuits the UC4 observe→act loop:
same outcome, fewer steps, lower cost. Most already exist as fast-paths
(Calendar/Mail/Notes/Contacts/Google, "open <website>", "open <app>"); this
module just *names* them and tags each FREE or PAID — the clean boundary the
product can later gate on, with **no billing logic here**. UC6 also adds a few
new skills (voice terminal commands, Cursor goto/symbol) registered the same way.

The registry is descriptive, not an execution path: dispatch still happens in the
existing handlers. `gate(name, plan)` is the single place a future paywall would
read; today it always allows and only reports the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    FREE = "free"   # always available
    PAID = "paid"   # gateable to a paid plan later (boundary only — not enforced)


@dataclass(frozen=True)
class Skill:
    name: str            # matches the fast-path action / handler name
    description: str
    tier: Tier
    category: str        # "system" | "comms" | "calendar" | "notes" | "web" | "dev"


# The catalog. Tiers are a sensible STARTING boundary, not a final pricing
# decision — flip a `Tier` here to move a skill across the paywall later.
SKILLS: dict[str, Skill] = {
    # --- free: basic local control (the "open any app/site" floor) ----------
    "open_app":       Skill("open_app", "Open a macOS app or folder", Tier.FREE, "system"),
    "open_url":       Skill("open_url", "Open a website in the browser", Tier.FREE, "web"),
    "open_note":      Skill("open_note", "Open a note in Notes", Tier.FREE, "notes"),
    "read_note":      Skill("read_note", "Read a note aloud", Tier.FREE, "notes"),
    "check_date":     Skill("check_date", "What's the date / day", Tier.FREE, "system"),
    "describe_screen": Skill("describe_screen", "Describe what's on screen", Tier.FREE, "system"),
    # --- paid: the orchestration + integrations that cost model/API spend ----
    "check_calendar": Skill("check_calendar", "Read your calendar (Apple + Google)", Tier.PAID, "calendar"),
    "create_event":   Skill("create_event", "Create a calendar event", Tier.PAID, "calendar"),
    "cancel_event":   Skill("cancel_event", "Cancel a calendar event", Tier.PAID, "calendar"),
    "check_mail":     Skill("check_mail", "Triage your inbox", Tier.PAID, "comms"),
    "draft_email":    Skill("draft_email", "Start an email to someone", Tier.PAID, "comms"),
    "save_contact":   Skill("save_contact", "Save a name → email", Tier.PAID, "comms"),
    "check_weather":  Skill("check_weather", "Weather / forecast", Tier.PAID, "system"),
    "research":       Skill("research", "Web research with cited results", Tier.PAID, "web"),
    "ui_task":        Skill("ui_task", "Multi-step on-screen task (the loop)", Tier.PAID, "system"),
    # --- new in UC6 ----------------------------------------------------------
    "run_command":    Skill("run_command", "Run a terminal command by voice", Tier.PAID, "dev"),
    "cursor_goto":    Skill("cursor_goto", "Open a file at a line in Cursor", Tier.PAID, "dev"),
    "cursor_symbol":  Skill("cursor_symbol", "Search a symbol in Cursor", Tier.PAID, "dev"),
    # --- new in Stage 2: voice-native Raycast (no-LLM search) ----------------
    "find_file":      Skill("find_file", "Find & open a file/doc via Spotlight", Tier.FREE, "system"),
    "open_settings":  Skill("open_settings", "Jump to a System Settings pane", Tier.FREE, "system"),
    "system_action":  Skill("system_action", "Lock/sleep/volume/trash + system actions", Tier.FREE, "system"),
}


def get(name: str) -> Optional[Skill]:
    return SKILLS.get(name)


def is_paid(name: str) -> bool:
    s = SKILLS.get(name)
    return s is not None and s.tier is Tier.PAID


def free_skills() -> list[Skill]:
    return [s for s in SKILLS.values() if s.tier is Tier.FREE]


def paid_skills() -> list[Skill]:
    return [s for s in SKILLS.values() if s.tier is Tier.PAID]


def gate(name: str, plan: str = "paid") -> dict:
    """The single seam a future paywall reads. `plan` ∈ {free, pro, ultra, …}.
    Today it never blocks — it only reports whether the skill WOULD be gated, so
    the boundary is testable now and enforcement is a one-line change later."""
    s = SKILLS.get(name)
    if s is None:
        return {"allowed": True, "known": False, "tier": None}
    # A paid skill is "gated" for a free plan; everything else is allowed.
    gated = s.tier is Tier.PAID and plan == "free"
    return {"allowed": True, "known": True, "tier": s.tier.value, "would_gate": gated}
