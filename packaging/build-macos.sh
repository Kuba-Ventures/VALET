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

NOTARY_PROFILE="${NOTARY_PROFILE:-valet-notary}"
if [ -z "${SIGNING_IDENTITY:-}" ]; then
  echo "NOTE: SIGNING_IDENTITY not set -> UNSIGNED build (local testing only; it"
  echo "      won't pass Gatekeeper for distribution). Set it for a release build."
  UNSIGNED=1
else
  UNSIGNED=0
fi

ARCH="$(uname -m)"   # arm64 | x86_64
case "$ARCH" in
  arm64)  TRIPLE="aarch64-apple-darwin" ;;
  x86_64) TRIPLE="x86_64-apple-darwin" ;;
  *) echo "unsupported arch: $ARCH"; exit 1 ;;
esac

echo "==> 0/5 app icon"
if [ ! -f src-tauri/icons/icon.icns ]; then
  # Seed from the orb PWA icon so a build works before a final logo exists.
  npm exec -- tauri icon assets/valet-icon.png
fi

echo "==> 1/5 frontend build"
( cd frontend && npm ci && npm run build )

echo "==> stamp this build (re-triggers onboarding on every new download)"
date +%Y%m%d%H%M%S > build_id.txt

echo "==> 2/5 PyInstaller backend"
# Invoke via `python -m` so a stale venv shebang (e.g. after a repo rename)
# can't break the build — the python symlink resolves even when console-script
# shebangs still point at an old path.
# --clean wipes the PyInstaller build cache every time: without it, an
# incremental build can bundle a stale server.py or frontend/dist (it did once).
./.venv/bin/python -m PyInstaller packaging/valet.spec --noconfirm --clean

echo "==> 3/5 place sidecar (Tauri requires the <name>-<target-triple> suffix)"
mkdir -p src-tauri/binaries
cp dist/valet-backend "src-tauri/binaries/valet-backend-${TRIPLE}"
chmod +x "src-tauri/binaries/valet-backend-${TRIPLE}"

echo "==> 4/5 tauri build"
if [ "$UNSIGNED" = "1" ]; then
  npm exec -- tauri build
else
  # Tauri signs the .app (and the sidecar) with the configured identity.
  APPLE_SIGNING_IDENTITY="$SIGNING_IDENTITY" npm exec -- tauri build
fi

APP="src-tauri/target/release/bundle/macos/VALET.app"
DMG="$(ls src-tauri/target/release/bundle/dmg/VALET_*.dmg 2>/dev/null | head -1 || true)"
echo "    built: $APP"

if [ "$UNSIGNED" = "1" ]; then
  echo
  echo "UNSIGNED build complete (skipping notarization)."
  echo "  Run it locally:  open \"$APP\"   (first launch: right-click -> Open)"
  echo "  For a release: set SIGNING_IDENTITY + rerun to sign + notarize."
  exit 0
fi

echo "==> 5/5 notarize + staple"
# Notarize the .dmg DIRECTLY (the distributed artifact). The .app inside is
# already signed, but the .app bundle is consumed during DMG creation, so
# notarizing the .app zip then stapling it fails ("VALET.app does not exist").
# Submitting the .dmg and stapling the ticket onto it is the correct path: it
# installs cleanly offline with no Gatekeeper warning.
if [ -z "${DMG:-}" ]; then
  echo "ERROR: no .dmg found at src-tauri/target/release/bundle/dmg/ to notarize"
  exit 1
fi
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG"

echo
echo "DONE. Verify Gatekeeper:"
echo "  spctl -a -vvv -t install \"$APP\""
echo "Upload the .dmg, then set DOWNLOAD_URL on the marketing site to its URL."
