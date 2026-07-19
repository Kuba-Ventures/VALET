#!/usr/bin/env bash
# Fast LOCAL redeploy of the backend into the installed VALET.app — for testing a
# code change against the real menu-bar app without a full DMG build.
#
#   ./packaging/deploy-local.sh              # rebuild backend, swap, sign, relaunch
#   ./packaging/deploy-local.sh --frontend   # also rebuild frontend/dist first
#   ./packaging/deploy-local.sh --no-build    # skip PyInstaller, deploy existing dist/valet-backend
#
# WHY THIS EXISTS (see CLAUDE.md "Testing a local build", warning #2):
# PyInstaller re-signs the backend AD-HOC — a *different code identity* than the
# Developer-ID one macOS granted Accessibility / Input Monitoring / Automation to.
# A bare `cp dist/valet-backend …` therefore silently loses those grants: the AX
# tree reads back empty (`ax_ok=False`) and features like the inbox digest report
# a false "nothing new" even though nothing is wrong with the code. This script
# re-signs the backend AND re-seals the bundle with your Developer ID after the
# swap, so the granted identity — and every TCC permission — survives.
#
# Env:
#   SIGNING_IDENTITY  override the signing identity (default: first "Developer ID
#                     Application" identity in your keychain)
#   VALET_APP         override the installed app path (default /Applications/VALET.app)
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

APP="${VALET_APP:-/Applications/VALET.app}"
ENT="src-tauri/entitlements.plist"

DO_FRONTEND=0
DO_BUILD=1
for arg in "$@"; do
  case "$arg" in
    --frontend) DO_FRONTEND=1 ;;
    --no-build) DO_BUILD=0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# Resolve a Developer ID Application identity (required — an ad-hoc/unsigned swap
# is the whole bug this script exists to avoid).
ID="${SIGNING_IDENTITY:-$(security find-identity -v -p codesigning \
  | awk -F'"' '/Developer ID Application/{print $2; exit}')}"
if [ -z "$ID" ]; then
  echo "ERROR: no 'Developer ID Application' identity found in your keychain." >&2
  echo "       Set SIGNING_IDENTITY=... explicitly, or install your Developer ID cert." >&2
  exit 1
fi
if [ ! -d "$APP" ]; then
  echo "ERROR: $APP not found. Install VALET first (or set VALET_APP)." >&2
  exit 1
fi
echo "==> signing identity: $ID"
echo "==> target app:       $APP"

if [ "$DO_FRONTEND" = "1" ]; then
  echo "==> rebuilding frontend"
  ( cd frontend && npm run build )
fi

if [ "$DO_BUILD" = "1" ]; then
  echo "==> rebuilding backend (PyInstaller)"
  ./.venv/bin/python -m PyInstaller packaging/valet.spec --noconfirm --clean
fi

if [ ! -f dist/valet-backend ]; then
  echo "ERROR: dist/valet-backend missing (run without --no-build first)." >&2
  exit 1
fi

echo "==> quitting the running app"
osascript -e 'quit app "VALET"' 2>/dev/null || true
pkill -f "$APP/Contents/MacOS/valet" 2>/dev/null || true
sleep 2

echo "==> swapping the backend binary"
cp dist/valet-backend "$APP/Contents/MacOS/valet-backend"

echo "==> re-signing backend + re-sealing bundle (Developer ID → keeps TCC grants)"
codesign --force --options runtime --entitlements "$ENT" --sign "$ID" \
  "$APP/Contents/MacOS/valet-backend"
codesign --force --options runtime --entitlements "$ENT" --sign "$ID" "$APP"
codesign --verify --verbose=2 "$APP"

echo "==> relaunching"
open -a VALET
echo "Done. Give the backend ~10s to warm up before the first command."
