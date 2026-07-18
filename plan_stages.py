"""
VALET Plan Stages — break a dispatched task into visible, live-tracked stages.

When VALET dispatches a "duteous" task to Claude Code, the Process Panel used to
show only a flat stream of tool calls. This module gives the panel a *plan*: a
short branching list of named stages (like Claude Code's own plan view) that
lights up stage-by-stage as the real work progresses.

Flow:
    stages = await generate_stages(prompt, task_type, client)
    tracker = StageTracker(task_id, stages, plan_title=name, client=client)
    await tracker.begin()                       # emit all stages (first active)
    loop = asyncio.create_task(run_tracker_loop(tracker))
    resp = await work.send(prompt, task_id=task_id, on_line=tracker.observe)
    loop.cancel()
    await tracker.finish(ok=...)                # mark the plan done / errored

`tracker.observe(line)` is a cheap synchronous buffer — it's called from the
Claude Code stream drain loop, so it must never block. `run_tracker_loop`
periodically asks Haiku which stage the buffered activity has reached and
advances the panel monotonically. Everything degrades gracefully: with no
`client`, or if Haiku fails, the stages simply stay put and all flip to done at
`finish()` — the plan still renders, it just doesn't self-advance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from process_events import emit_plan_stage

log = logging.getLogger("valet.plan_stages")

# Background classify cadence + throttles. A classify is only worth doing once a
# meaningful amount of fresh activity has streamed in, so calls scale with output
# volume rather than wall-clock time.
STAGE_TICK_SECONDS = 4.0
MIN_NEW_CHARS = 400        # need at least this much new activity to reclassify
CLASSIFY_WINDOW = 1600     # tail of the activity buffer handed to Haiku
MAX_CLASSIFY_CALLS = 40    # hard backstop against a pathological long build

# Generic fallback stages per task type, used when Haiku can't (or needn't)
# author a bespoke plan. Kept to 3-4 short imperative phrases.
_DEFAULT_STAGES: dict[str, list[str]] = {
    "build":    ["Scaffold project", "Build core logic", "Create the UI", "Wire up & test"],
    "feature":  ["Map the code", "Implement the feature", "Wire it in", "Verify"],
    "fix":      ["Reproduce the issue", "Find the cause", "Apply the fix", "Verify"],
    "research": ["Gather sources", "Read & extract", "Synthesize findings", "Write it up"],
    "refactor": ["Map the code", "Plan the changes", "Refactor", "Verify behavior"],
    "run":      ["Prepare", "Run it", "Report back"],
    "ui":       ["Open the app", "Navigate", "Do the task", "Wrap up"],
}
_GENERIC_STAGES = ["Get oriented", "Do the work", "Verify"]

MIN_STAGES = 3
MAX_STAGES = 6


def default_stages(task_type: str) -> list[str]:
    return list(_DEFAULT_STAGES.get(task_type, _GENERIC_STAGES))


async def generate_stages(
    prompt: str,
    task_type: str = "build",
    client: Optional[Any] = None,
) -> list[str]:
    """Break a task into 3-6 short, sequential, human-readable stages.

    Uses Haiku when a client is given; otherwise (or on any failure) returns a
    generic per-task-type plan. Always returns between MIN_STAGES and MAX_STAGES
    non-empty phrases.
    """
    if not client:
        return default_stages(task_type)

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=(
                "You turn a user's task into a short plan for a live progress "
                "display. Break the work into 3 to 5 SEQUENTIAL steps.\n"
                "Each step: a 2-5 word imperative phrase naming a real milestone, "
                "in the task's OWN domain. For a coding task that might be "
                "'Scaffold project', 'Build data layer', 'Wire up & test'. For an "
                "app or browser task, name the actual steps a person would take, "
                "e.g. 'Open Gmail', 'Sign in', 'Summarize inbox' — NOT coding "
                "stages. Order them the way the work actually happens.\n"
                "Respond with JSON only, no markdown fences: "
                '{"stages": ["...", "..."]}'
            ),
            messages=[{"role": "user", "content": prompt[:2000]}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        stages = [str(s).strip() for s in data.get("stages", []) if str(s).strip()]
        stages = stages[:MAX_STAGES]
        if len(stages) >= MIN_STAGES:
            return stages
    except Exception as e:
        log.warning("generate_stages failed, using defaults: %s", e)

    return default_stages(task_type)


class StageTracker:
    """Tracks and broadcasts progress through a plan's stages.

    `observe` buffers streamed activity synchronously (cheap). `tick` (driven by
    `run_tracker_loop`) periodically classifies the buffer with Haiku and
    advances the active stage forward — never backward. `finish` closes the plan
    out, marking remaining stages done (or the current one errored).
    """

    def __init__(
        self,
        task_id: str,
        stages: list[str],
        plan_title: str = "",
        client: Optional[Any] = None,
    ) -> None:
        self.task_id = task_id
        self.stages = stages or list(_GENERIC_STAGES)
        self.plan_title = plan_title
        self.client = client
        self.current = 0
        self._done = False
        self._buf: list[str] = []
        self._buf_chars = 0
        self._chars_at_last_classify = 0
        self._classify_calls = 0

    @property
    def total(self) -> int:
        return len(self.stages)

    async def _emit(self, index: int, status: str) -> None:
        try:
            await emit_plan_stage(
                self.task_id, index, self.total, self.stages[index],
                status=status, plan_title=self.plan_title,
            )
        except Exception:
            # Panel emission is best-effort — never let it break a build.
            pass

    async def begin(self) -> None:
        """Emit every stage: the first active, the rest pending."""
        for i in range(self.total):
            await self._emit(i, "active" if i == 0 else "pending")

    def observe(self, line: str) -> None:
        """Buffer a line of distilled Claude Code activity. Sync + cheap."""
        if not line:
            return
        self._buf.append(line)
        self._buf_chars += len(line)

    async def _advance_to(self, idx: int) -> None:
        idx = min(idx, self.total - 1)
        if idx <= self.current or self._done:
            return
        # Mark every stage we're passing as done, then light the new one.
        for i in range(self.current, idx):
            await self._emit(i, "done")
        await self._emit(idx, "active")
        self.current = idx

    async def tick(self) -> None:
        """One classify-and-maybe-advance beat. Safe to call frequently."""
        if self._done or not self.client:
            return
        if self.current >= self.total - 1:
            return  # already on the last stage; nothing to advance to
        new_chars = self._buf_chars - self._chars_at_last_classify
        if new_chars < MIN_NEW_CHARS:
            return
        if self._classify_calls >= MAX_CLASSIFY_CALLS:
            return
        self._chars_at_last_classify = self._buf_chars
        self._classify_calls += 1

        activity = "\n".join(self._buf)[-CLASSIFY_WINDOW:]
        idx = await self._classify(activity)
        if idx is not None:
            await self._advance_to(idx)

    async def _classify(self, activity: str) -> Optional[int]:
        """Ask Haiku which stage the streamed activity has reached (0-based)."""
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(self.stages))
        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                system=(
                    "You watch a coding agent's live activity log and report which "
                    "planned stage it is currently working on.\n\n"
                    f"The plan stages are:\n{numbered}\n\n"
                    "Reply with ONLY the integer index (0-based) of the stage the "
                    "MOST RECENT activity belongs to. If unsure, reply with the "
                    "current or earliest plausible index. No words, just the number."
                ),
                messages=[{"role": "user", "content": activity or "(no activity yet)"}],
            )
            raw = response.content[0].text.strip()
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not digits:
                return None
            idx = int(digits[:2])
            return max(0, min(idx, self.total - 1))
        except Exception as e:
            log.debug("stage classify failed: %s", e)
            return None

    async def finish(self, ok: bool = True) -> None:
        """Close the plan out. ok=False marks the active stage as errored."""
        if self._done:
            return
        self._done = True
        if ok:
            for i in range(self.current, self.total):
                await self._emit(i, "done")
        else:
            await self._emit(self.current, "error")


async def run_tracker_loop(tracker: StageTracker) -> None:
    """Drive a tracker's periodic ticks until cancelled. Spawn with
    asyncio.create_task and cancel once the underlying work finishes."""
    try:
        while True:
            await asyncio.sleep(STAGE_TICK_SECONDS)
            await tracker.tick()
    except asyncio.CancelledError:
        pass
    except Exception as e:  # defensive: a ticker crash must not surface
        log.debug("tracker loop stopped: %s", e)
