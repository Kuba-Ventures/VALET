#!/usr/bin/env bash
# Collapse duplicate VALET subscriptions to a single comp Ultra. Shows a
# DRY-RUN plan and only cancels after you type "yes". Two modes:
#
#   bash scripts/cleanup-comp-dupes.sh <email>   # subscriptions on one email
#   bash scripts/cleanup-comp-dupes.sh sweep     # ALL comp subs on the account
#                                                # (catches emailless orphans)
#
set -euo pipefail
cd "$(dirname "$0")/.."

ARG="${1:-}"
if [ -z "$ARG" ]; then
  echo "Usage: bash scripts/cleanup-comp-dupes.sh <email|sweep>" >&2
  exit 1
fi

read -rsp "Paste STRIPE_SECRET_KEY (hidden): " STRIPE_SECRET_KEY
echo
export STRIPE_SECRET_KEY
if [ "$ARG" = "sweep" ]; then
  export SWEEP=1
  echo "Mode: SWEEP (all comp subscriptions on the account)"
else
  export TARGET_EMAIL="$ARG"
  echo "Target email: $TARGET_EMAIL"
fi

echo
echo "=== DRY-RUN ==="
node scripts/cleanup-comp-dupes.mjs

echo
read -rp 'Type "yes" to cancel the CANCEL-tagged subscriptions above: ' CONFIRM
if [ "$CONFIRM" = "yes" ]; then
  echo "=== APPLYING ==="
  APPLY=1 node scripts/cleanup-comp-dupes.mjs
else
  echo "Aborted. Nothing was canceled."
fi
