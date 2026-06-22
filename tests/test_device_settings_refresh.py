"""Live device-settings refresh (backend): the app re-applies web-controlled
settings while running, so a change saved on the account dashboard (e.g. the
voice) takes effect without a restart — but only when the web value actually
changed, so a poll never clobbers an in-app toggle.

Run:  ./.venv/bin/python tests/test_device_settings_refresh.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import server


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def json(self):
        return self._p


class _Client:
    """Stub httpx.AsyncClient whose GET returns whatever `payload` is set to."""

    payload = {"settings": {}}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return _Resp(_Client.payload)


def _setup():
    server.httpx.AsyncClient = _Client
    server.LICENSE_KEY = "TEST-KEY"
    server._last_web_device_settings = None
    writes: list = []
    server._write_env_key = lambda k, v: writes.append((k, v))
    return writes


def test_applies_when_web_value_changes():
    writes = _setup()

    async def run():
        _Client.payload = {"settings": {"voice": "female"}}
        await server._fetch_and_apply_device_settings()
        assert ("VALET_VOICE", "female") in writes, writes

        # A later web change is applied.
        _Client.payload = {"settings": {"voice": "male"}}
        await server._fetch_and_apply_device_settings()
        assert ("VALET_VOICE", "male") in writes, writes

    asyncio.run(run())


def test_noop_when_unchanged_does_not_clobber():
    writes = _setup()

    async def run():
        _Client.payload = {"settings": {"voice": "female"}}
        await server._fetch_and_apply_device_settings()
        n = len(writes)
        # Same web value on the next poll -> no re-apply (so an in-app toggle
        # made meanwhile is left alone).
        await server._fetch_and_apply_device_settings()
        assert len(writes) == n, f"expected no new writes, got {writes[n:]}"

    asyncio.run(run())


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
