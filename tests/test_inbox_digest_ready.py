"""inbox_digest readiness gate — the digest must wait for Gmail's conversation
list to render into the AX tree before reading, or it reports a false "nothing
new today" on an inbox full of mail (issue #285 follow-up).

Run:  ./.venv/bin/python tests/test_inbox_digest_ready.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import inbox_digest
import perception


def _obs(n_rows):
    els = [{"role": "AXRow", "title": f"row {i}"} for i in range(n_rows)]
    els += [{"role": "AXButton", "title": "Compose"}]
    return {"elements": els}


def test_count_ax_rows():
    assert inbox_digest._count_ax_rows(_obs(0)) == 0
    assert inbox_digest._count_ax_rows(_obs(6)) == 6
    assert inbox_digest._count_ax_rows({}) == 0


def test_await_inbox_ready_waits_then_returns_rows():
    # First two observations are still-loading (no rows); the third has rendered.
    # _await_inbox_ready must keep polling and return the populated observation,
    # not the empty first frame.
    seq = [_obs(0), _obs(0), _obs(6), _obs(6)]
    calls = {"n": 0}

    async def fake_build(executor, app=None, **kw):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    orig = perception.build_observation
    perception.build_observation = fake_build
    try:
        obs = asyncio.run(
            inbox_digest._await_inbox_ready(None, "Google Chrome", lambda: False,
                                            tries=8, delay=0.0))
    finally:
        perception.build_observation = orig
    assert inbox_digest._count_ax_rows(obs) == 6, obs
    assert calls["n"] >= 3, calls


def test_await_inbox_ready_gives_up_when_never_ready():
    # A genuinely empty inbox never grows rows — the loop must terminate (return
    # the empty observation) rather than hang.
    async def fake_build(executor, app=None, **kw):
        return _obs(0)

    orig = perception.build_observation
    perception.build_observation = fake_build
    try:
        obs = asyncio.run(
            inbox_digest._await_inbox_ready(None, "Google Chrome", lambda: False,
                                            tries=4, delay=0.0))
    finally:
        perception.build_observation = orig
    assert inbox_digest._count_ax_rows(obs) == 0


def test_await_inbox_ready_bails_on_halt():
    # A halt signal short-circuits the wait immediately.
    async def fake_build(executor, app=None, **kw):
        return _obs(0)

    orig = perception.build_observation
    perception.build_observation = fake_build
    try:
        obs = asyncio.run(
            inbox_digest._await_inbox_ready(None, "Google Chrome", lambda: True,
                                            tries=99, delay=0.0))
    finally:
        perception.build_observation = orig
    assert obs is not None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
