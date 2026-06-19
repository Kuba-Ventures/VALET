"""Stage 2 — visible cursor glide: headless unit tests.

`move_cursor` takes injectable clock/sleep/read_pos/post_move, so the tween,
the physical-mouse-grab abort, and the timeout are all exercised deterministically
with no real cursor. On-screen smoothness is a manual-on-device check (you can't
unit-test perceived motion), so it's deliberately out of scope here.

Run:  ./.venv/bin/python tests/test_move_cursor.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import accessibility_executor as ax


class _Cursor:
    """A cursor that obeys our move commands — the no-interference happy path."""

    def __init__(self, start=(0.0, 0.0)):
        self.pos = start
        self.posted = []

    def read(self):
        return self.pos

    def post(self, x, y):
        self.posted.append((x, y))
        self.pos = (x, y)


class _GrabCursor(_Cursor):
    """Cursor that the *user* yanks elsewhere after `jump_after` reads."""

    def __init__(self, jump_after, jump_to):
        super().__init__()
        self.reads = 0
        self.jump_after = jump_after
        self.jump_to = jump_to

    def read(self):
        self.reads += 1
        return self.jump_to if self.reads > self.jump_after else self.pos


def _steady_clock(step=0.001):
    t = {"v": 0.0}

    def c():
        v = t["v"]
        t["v"] += step
        return v
    return c


def _seq_clock(vals):
    it = iter(vals)
    return lambda: next(it)


def _noop(_):
    pass


def test_arrives_at_target():
    c = _Cursor((0.0, 0.0))
    res = ax.move_cursor(100.0, 50.0, duration=0.3, fps=10,
                         clock=_steady_clock(), sleep=_noop,
                         read_pos=c.read, post_move=c.post)
    assert res["arrived"] is True and res["aborted"] is None
    assert c.posted, "should have posted move events"
    lx, ly = c.posted[-1]
    assert abs(lx - 100.0) < 1e-6 and abs(ly - 50.0) < 1e-6  # smoothstep lands on target


def test_motion_is_monotonic_toward_target():
    c = _Cursor((0.0, 0.0))
    ax.move_cursor(100.0, 0.0, duration=0.5, fps=20,
                   clock=_steady_clock(), sleep=_noop, read_pos=c.read, post_move=c.post)
    xs = [p[0] for p in c.posted]
    assert xs == sorted(xs), "eased motion should advance toward the target, never backward"
    assert 0.0 < xs[0] < xs[-1] == 100.0


def test_user_grab_aborts_immediately():
    c = _GrabCursor(jump_after=1, jump_to=(900.0, 900.0))
    res = ax.move_cursor(100.0, 50.0, duration=0.3, fps=10,
                         clock=_steady_clock(), sleep=_noop,
                         read_pos=c.read, post_move=c.post)
    assert res["arrived"] is False and res["aborted"] == "user_moved"
    # It must stop posting once the user takes over (no fighting for the cursor).
    assert len(c.posted) <= 2


def test_timeout_aborts():
    c = _Cursor((0.0, 0.0))
    # clock: deadline = 0.0 + 0.3 + 0.5 = 0.8; first frame's check reads 999 > 0.8.
    res = ax.move_cursor(100.0, 50.0, duration=0.3, fps=10,
                         clock=_seq_clock([0.0, 999.0]), sleep=_noop,
                         read_pos=c.read, post_move=c.post)
    assert res["arrived"] is False and res["aborted"] == "timeout"


def test_glide_helpers_exist_for_wiring():
    # The executor wrappers the server calls into must be present.
    ex = ax.AccessibilityExecutor()
    assert hasattr(ex, "glide_to_target") and hasattr(ex, "_verify_under")


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
