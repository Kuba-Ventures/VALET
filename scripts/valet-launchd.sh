#!/bin/bash
# Wrapper invoked by launchd. server.py loads .env itself via its own parser,
# so we don't shell-source it here (shell sourcing chokes on values with
# spaces or commas, e.g. an address like "Martinsville, VA").

set -eu

VALET_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$VALET_DIR"
mkdir -p logs
exec .venv/bin/python server.py
