#!/usr/bin/env bash
# Collapse duplicate VALET subscriptions for one email to a single comp Ultra.
# Prompts for the Stripe key (hidden) + email, shows a DRY-RUN plan, and only
# cancels after you type "yes".
#
#   bash scripts/cleanup-comp-dupes.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

read -rsp "Paste STRIPE_SECRET_KEY (hidden): " STRIPE_SECRET_KEY
echo
read -rp "Target email to clean up: " TARGET_EMAIL
export STRIPE_SECRET_KEY TARGET_EMAIL

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
