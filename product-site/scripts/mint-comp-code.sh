#!/usr/bin/env bash
# Mint the 100%-off-forever VIP comp code in Stripe.
# Pulls the real Stripe creds from Vercel (Production env), then runs the
# generator. Safe to re-run: the coupon + code are reused, not duplicated.
#
#   bash scripts/mint-comp-code.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

ENVFILE=".env.stripe"
echo "Pulling Production env from the linked Vercel project…"
vercel env pull --environment=production "$ENVFILE" >/dev/null

set -a
# shellcheck disable=SC1090
source "$ENVFILE"
set +a

# Vercel returns Sensitive vars empty on `env pull`, so fall back to manual
# entry. The secret key is read with -s (no echo) and never hits shell history.
if [ -z "${STRIPE_SECRET_KEY:-}" ]; then
  echo
  echo "STRIPE_SECRET_KEY came down empty (it's Sensitive in Vercel)."
  echo "Get it from Stripe Dashboard > Developers > API keys (use LIVE mode)."
  read -rsp "Paste STRIPE_SECRET_KEY (hidden): " STRIPE_SECRET_KEY
  echo
  export STRIPE_SECRET_KEY
fi
if [ -z "${STRIPE_PRICE_ID_ULTRA:-}" ]; then
  echo "STRIPE_PRICE_ID_ULTRA came down empty."
  echo "Get it from Stripe Dashboard > Products > VALET Ultra > the \$50/mo price (price_...)."
  read -rp "Paste STRIPE_PRICE_ID_ULTRA: " STRIPE_PRICE_ID_ULTRA
  export STRIPE_PRICE_ID_ULTRA
fi
if [ -z "${STRIPE_SECRET_KEY:-}" ] || [ -z "${STRIPE_PRICE_ID_ULTRA:-}" ]; then
  echo "ERROR: still missing one of the values. Aborting."
  rm -f "$ENVFILE"
  exit 1
fi

case "$STRIPE_SECRET_KEY" in
  *_live_*) echo ">>> LIVE Stripe (creates REAL objects)." ;;
  *)        echo ">>> TEST Stripe (rehearsal only; grants no real Ultra license)." ;;
esac

COMP_CODE="${COMP_CODE:-VALETVIP}" node scripts/create-comp-promo.mjs

echo
echo "Cleaning up pulled secrets ($ENVFILE)…"
rm -f "$ENVFILE"
