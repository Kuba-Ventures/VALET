#!/bin/bash
# One-time installer for the VALET launchd user agent.
# Re-running is safe — it tears down any existing instance first.

set -eu

VALET_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.valet.backend"
TEMPLATE="$VALET_DIR/scripts/$LABEL.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

# Sanity checks
if [ ! -x "$VALET_DIR/.venv/bin/python" ]; then
    echo "ERROR: Python venv not found at $VALET_DIR/.venv"
    echo "       Run setup first (python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt)"
    exit 1
fi
if [ ! -f "$VALET_DIR/server.py" ]; then
    echo "ERROR: server.py missing in $VALET_DIR"
    exit 1
fi
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: plist template missing at $TEMPLATE"
    exit 1
fi

mkdir -p "$VALET_DIR/logs"
mkdir -p "$HOME/Library/LaunchAgents"

chmod +x "$VALET_DIR/scripts/valet-launchd.sh"

# Render the template with current absolute paths (project dir + $HOME so the
# launchd PATH can reach ~/.local/bin where some user-scope CLIs like claude live).
sed -e "s|__VALET_DIR__|$VALET_DIR|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$PLIST_PATH"

# Idempotent reload: remove any existing instance, then bootstrap fresh
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"
launchctl enable   "gui/$UID/$LABEL"
launchctl kickstart -k "gui/$UID/$LABEL"

echo
echo "VALET backend installed as launchd user agent."
echo
echo "  Label:      $LABEL"
echo "  Plist:      $PLIST_PATH"
echo "  Project:    $VALET_DIR"
echo "  Logs:       $VALET_DIR/logs/valet.{out,err}.log"
echo
echo "It will auto-start on every login and restart on crash."
echo
echo "Useful commands:"
echo "  Status:     launchctl print gui/\$UID/$LABEL | head -30"
echo "  PID/state:  launchctl list | grep $LABEL"
echo "  Tail logs:  tail -f $VALET_DIR/logs/valet.err.log"
echo "  Restart:    launchctl kickstart -k gui/\$UID/$LABEL"
echo "  Uninstall:  $VALET_DIR/scripts/uninstall-launchd.sh"
