"""Process restart utility.

Lives OUTSIDE self_mod (which is excluded from shipped builds) so "restart
yourself" works in any build.

- Dev: spawns scripts/restart.sh detached; a supervisor relaunches the process.
- Packaged (VALET_SHIPPED): the Tauri shell owns the backend lifecycle and
  respawns the sidecar when it exits (see src-tauri/src/main.rs), so we just
  exit cleanly a moment after the confirmation is spoken.
"""

import os
import subprocess
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
RESTART_SCRIPT = _HERE / "scripts" / "restart.sh"


def restart_self() -> dict:
    """Restart the backend. Returns {success, message}. The caller should speak
    its confirmation BEFORE this returns — the process is about to go away."""
    # Packaged build: exit cleanly; the Tauri shell relaunches the sidecar.
    if os.environ.get("VALET_SHIPPED"):
        def _exit_soon() -> None:
            time.sleep(1.0)  # give the spoken confirmation time to send
            os._exit(0)
        threading.Thread(target=_exit_soon, daemon=True).start()
        return {"success": True, "message": "Restarting, sir. One moment."}

    # Dev: hand off to the supervisor script, then it kills this process.
    if not RESTART_SCRIPT.exists():
        return {"success": False, "message": f"missing {RESTART_SCRIPT}"}
    try:
        subprocess.Popen(
            [str(RESTART_SCRIPT)],
            cwd=str(_HERE),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"success": True, "message": "Restarter spawned. Goodnight for ~5s, sir."}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}
