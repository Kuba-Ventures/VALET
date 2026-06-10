#!/bin/bash
# VALET smoke test — used by the self-modification flow as the gate
# before merging a feature branch to main.
#
# Three checks, all fast (< 60s total):
#   1. Python modules import cleanly.
#   2. FastAPI app constructs + /api/health returns 200 (via TestClient —
#      no port binding, validates the NEW code on disk rather than a
#      currently-running stale process).
#   3. Frontend build (npm run build) exits 0.
#
# Exits 0 on success, non-zero on any failure with the failing step echoed.
# Stdout/stderr deliberately verbose so the design_partner flow can capture
# them and speak them back if smoke fails.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "[smoke 1/3] python imports..."
.venv/bin/python -c "import server, design_partner, work_mode, project_context, memory, actions, claude_middleware, self_mod" \
  && echo "  OK" \
  || { echo "  FAIL: python imports broken"; exit 11; }

echo "[smoke 2/3] FastAPI app + /api/health via TestClient..."
.venv/bin/python <<'PY' || { echo "  FAIL: /api/health did not return 200"; exit 12; }
from fastapi.testclient import TestClient
from server import app
with TestClient(app) as c:
    r = c.get("/api/health")
    assert r.status_code == 200, f"/api/health returned {r.status_code}"
print("  OK")
PY

echo "[smoke 3/3] frontend build..."
cd "$ROOT/frontend"
if ! npm run build > /tmp/valet-smoke-build.log 2>&1; then
  echo "  FAIL: npm run build exited non-zero"
  tail -30 /tmp/valet-smoke-build.log
  exit 13
fi
echo "  OK"

echo "smoke OK"
