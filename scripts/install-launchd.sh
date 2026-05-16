#!/bin/bash
# One-time installer for the JARVIS launchd user agent.
# Re-running is safe — it tears down any existing instance first.

set -eu

JARVIS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.jarvis.backend"
TEMPLATE="$JARVIS_DIR/scripts/$LABEL.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

# Sanity checks
if [ ! -x "$JARVIS_DIR/.venv/bin/python" ]; then
    echo "ERROR: Python venv not found at $JARVIS_DIR/.venv"
    echo "       Run setup first (python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt)"
    exit 1
fi
if [ ! -f "$JARVIS_DIR/server.py" ]; then
    echo "ERROR: server.py missing in $JARVIS_DIR"
    exit 1
fi
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: plist template missing at $TEMPLATE"
    exit 1
fi

mkdir -p "$JARVIS_DIR/logs"
mkdir -p "$HOME/Library/LaunchAgents"

chmod +x "$JARVIS_DIR/scripts/jarvis-launchd.sh"

# Render the template with current absolute paths (project dir + $HOME so the
# launchd PATH can reach ~/.local/bin where some user-scope CLIs like claude live).
sed -e "s|__JARVIS_DIR__|$JARVIS_DIR|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$PLIST_PATH"

# Idempotent reload: remove any existing instance, then bootstrap fresh
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"
launchctl enable   "gui/$UID/$LABEL"
launchctl kickstart -k "gui/$UID/$LABEL"

echo
echo "JARVIS backend installed as launchd user agent."
echo
echo "  Label:      $LABEL"
echo "  Plist:      $PLIST_PATH"
echo "  Project:    $JARVIS_DIR"
echo "  Logs:       $JARVIS_DIR/logs/jarvis.{out,err}.log"
echo
echo "It will auto-start on every login and restart on crash."
echo
echo "Useful commands:"
echo "  Status:     launchctl print gui/\$UID/$LABEL | head -30"
echo "  PID/state:  launchctl list | grep $LABEL"
echo "  Tail logs:  tail -f $JARVIS_DIR/logs/jarvis.err.log"
echo "  Restart:    launchctl kickstart -k gui/\$UID/$LABEL"
echo "  Uninstall:  $JARVIS_DIR/scripts/uninstall-launchd.sh"
