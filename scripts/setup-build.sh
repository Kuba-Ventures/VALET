#!/usr/bin/env bash
# One-shot build-environment setup. Run once before the first packaging build:
#
#   bash scripts/setup-build.sh
#
# Does two things:
#  1. Repairs stale venv console-script shebangs left over from the repo rename
#     (jarvis-main -> VALET), so ./.venv/bin/pip etc. work again.
#  2. Installs PyInstaller (the backend bundler) into the venv.
set -euo pipefail
cd "$(dirname "$0")/.."
HERE="$(pwd)"
PY="$HERE/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "No .venv found at $HERE/.venv — create it first: python3 -m venv .venv"
  exit 1
fi

echo "==> repairing venv shebangs -> $HERE/.venv"
for f in .venv/bin/*; do
  [ -f "$f" ] || continue
  # Rewrite any '#!.../.venv/bin/python...' first line to this repo's path,
  # preserving the version suffix (e.g. python3.12).
  if head -1 "$f" 2>/dev/null | grep -q '^#!.*/\.venv/bin/python'; then
    sed -i '' "1s|^#!.*/\.venv/bin/python|#!$HERE/.venv/bin/python|" "$f" || true
  fi
done

echo "==> installing pyinstaller"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet pyinstaller

echo "OK. pyinstaller $("$PY" -m PyInstaller --version 2>/dev/null) ready."
echo "Next: tauri icon <1024px.png>, then SIGNING_IDENTITY=... ./packaging/build-macos.sh"
