"""Connection pre-warm / shared HTTP client — headless unit tests (PR 4).

Covers the shared keep-alive client singleton (created once, reused, reopened
after close) and the extracted static-system block's stability (two builds are
byte-identical, so a startup pre-warm hits the same prompt cache the live turn
does). No network.

Run:  ./.venv/bin/python -m pytest tests/test_prewarm.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import server


def test_http_client_is_a_reused_singleton():
    asyncio.run(server._close_http_client())  # start clean
    c1 = server._get_http_client()
    c2 = server._get_http_client()
    assert c1 is c2                       # same pooled client → keep-alive reused
    assert not c1.is_closed
    asyncio.run(server._close_http_client())


def test_http_client_reopens_after_close():
    c1 = server._get_http_client()
    asyncio.run(server._close_http_client())
    assert c1.is_closed
    c2 = server._get_http_client()
    assert c2 is not c1 and not c2.is_closed   # a fresh client after shutdown
    asyncio.run(server._close_http_client())


def test_http_client_configured():
    import httpx
    c = server._get_http_client()
    assert isinstance(c, httpx.AsyncClient)
    # Connect timeout is set (the handshake bound); public surface only.
    assert c.timeout.connect == 5.0
    asyncio.run(server._close_http_client())


def test_static_system_is_stable_and_nonempty():
    a = server._static_system()
    b = server._static_system()
    assert isinstance(a, str) and a.strip()
    assert a == b   # byte-identical → the pre-warm warms the live turn's cache prefix
