#!/bin/bash
# Stops and removes the JARVIS launchd user agent.
# Leaves logs/ and .env in place; delete those manually if desired.

set -u

LABEL="com.jarvis.backend"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl bootout "gui/$UID/$LABEL" 2>/dev/null; then
    echo "Stopped and unloaded $LABEL"
else
    echo "Service was not currently loaded (nothing to stop)."
fi

if [ -f "$PLIST_PATH" ]; then
    rm "$PLIST_PATH"
    echo "Removed $PLIST_PATH"
fi

echo "Done. Logs preserved under jarvis/logs/ if you need them."
