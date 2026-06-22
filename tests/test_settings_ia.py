"""Settings IA invariants (backend): billing is web-owned and Input Monitoring is
surfaced.

- ACCOUNT_PLAN must NOT be writable via /api/settings/keys (invariant 2 — plan is
  set only by /api/account/login from the server's truth, never forged locally).
- /api/permissions/status must include input_monitoring (the ⌃⌥ chord grant) with
  a settings deep link.

Run:  ./.venv/bin/python tests/test_settings_ia.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import server


def test_account_plan_not_writable():
    body = server.KeyUpdate(key_name="ACCOUNT_PLAN", key_value="ultra")
    resp = asyncio.run(server.api_settings_keys(body))
    # Rejected with a 400 JSONResponse, not written.
    assert getattr(resp, "status_code", None) == 400, resp


def test_a_known_key_is_still_writable_shape():
    # Sanity: a benign allowed key returns success-shaped (writes USER_NAME).
    body = server.KeyUpdate(key_name="USER_NAME", key_value="Finley")
    resp = asyncio.run(server.api_settings_keys(body))
    assert isinstance(resp, dict) and resp.get("success") is True, resp


def test_permissions_status_has_input_monitoring():
    status = asyncio.run(server.api_permissions_status())
    assert "input_monitoring" in status, list(status.keys())
    im = status["input_monitoring"]
    assert im["label"] == "Input Monitoring"
    assert im["settings_pane"].endswith("Privacy_ListenEvent")


def test_settings_panes_has_input_monitoring():
    assert server._SETTINGS_PANES["input_monitoring"].endswith("Privacy_ListenEvent")


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
