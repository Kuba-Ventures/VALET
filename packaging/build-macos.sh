#!/usr/bin/env bash
# Build the signed, notarized VALET macOS app.
#
#   ./packaging/build-macos.sh
#
# Pipeline: build frontend -> PyInstaller backend -> place as Tauri sidecar ->
# tauri build (bundles + signs) -> notarize + staple.
#
# Prerequisites (install once):
#   - Rust:        https://rustup.rs
#   - Tauri CLI:   npm i -g @tauri-apps/cli   (v2)
#   - PyInstaller: ./.venv/bin/pip install pyinstaller
#   - Apple Developer ID Application cert in your login keychain
#   - A notarytool keychain profile:
#       xcrun notarytool store-credentials valet-notary \
#         --apple-id "<you@apple.id>" --team-id "<TEAMID>" --password "<app-specific-pw>"
#
# Env (set before running):
#   SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE="valet-notary"
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

: "${SIGNING_IDENTITY:?Set SIGNING_IDENTITY to your Developer ID Application identity}"
: "${NOTARY_PROFILE:=valet-notary}"

ARCH="$(uname -m)"   # arm64 | x86_64
case "$ARCH" in
  arm64)  TRIPLE="aarch64-apple-darwin" ;;
  x86_64) TRIPLE="x86_64-apple-darwin" ;;
  *) echo "unsupported arch: $ARCH"; exit 1 ;;
esac

echo "==> 1/5 frontend build"
( cd frontend && npm ci && npm run build )

echo "==> 2/5 PyInstaller backend"
# Invoke via `python -m` so a stale venv shebang (e.g. after a repo rename)
# can't break the build — the python symlink resolves even when console-script
# shebangs still point at an old path.
./.venv/bin/python -m PyInstaller packaging/valet.spec --noconfirm

echo "==> 3/5 place sidecar (Tauri requires the <name>-<target-triple> suffix)"
mkdir -p src-tauri/binaries
cp dist/valet-backend "src-tauri/binaries/valet-backend-${TRIPLE}"
chmod +x "src-tauri/binaries/valet-backend-${TRIPLE}"

echo "==> 4/5 tauri build (bundle + sign)"
# Tauri signs the .app (and the sidecar) with the configured identity.
APPLE_SIGNING_IDENTITY="$SIGNING_IDENTITY" \
  npm --prefix . exec -- tauri build

APP="src-tauri/target/release/bundle/macos/VALET.app"
DMG="$(ls src-tauri/target/release/bundle/dmg/VALET_*.dmg 2>/dev/null | head -1 || true)"
echo "    built: $APP"

echo "==> 5/5 notarize + staple"
ZIP="$(mktemp -d)/VALET.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"
[ -n "${DMG:-}" ] && xcrun stapler staple "$DMG" || true

echo
echo "DONE. Verify Gatekeeper:"
echo "  spctl -a -vvv -t install \"$APP\""
echo "Upload the .dmg, then set DOWNLOAD_URL on the marketing site to its URL."
