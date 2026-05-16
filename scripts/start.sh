#!/bin/bash
# Reliable launcher for JARVIS backend + frontend dev servers.
# Survives terminal exit. Run from anywhere:  bash scripts/start.sh
#
# Each service is launched via `nohup ... </dev/null >log 2>&1 &` so it fully
# detaches from the launching shell — closing your terminal won't kill it.

set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# ---- Backend ---------------------------------------------------------------
if lsof -nP -iTCP:8340 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[start] backend already running on :8340"
else
  echo "[start] launching backend (server.py) on :8340"
  nohup .venv/bin/python server.py </dev/null >logs/jarvis.out.log 2>logs/jarvis.err.log &
  disown $! 2>/dev/null || true
fi

# ---- Frontend --------------------------------------------------------------
if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[start] frontend already running on :5173"
else
  echo "[start] launching frontend (Vite) on :5173"
  cd "$REPO/frontend"
  nohup npm run dev </dev/null >/tmp/jarvis-vite.log 2>&1 &
  disown $! 2>/dev/null || true
  cd "$REPO"
fi

# Wait for both to come up
echo "[start] waiting for services…"
for i in {1..15}; do
  back=$(lsof -nP -iTCP:8340 -sTCP:LISTEN 2>/dev/null | tail -n +2)
  front=$(lsof -nP -iTCP:5173 -sTCP:LISTEN 2>/dev/null | tail -n +2)
  if [ -n "$back" ] && [ -n "$front" ]; then
    echo "[start] ✓ both up — open http://localhost:5173"
    exit 0
  fi
  sleep 1
done

echo "[start] timed out waiting; check logs/jarvis.err.log and /tmp/jarvis-vite.log"
exit 1
