#!/usr/bin/env node
/**
 * Create (idempotently) the VIP comp discount and a shareable promotion code:
 *   - a coupon: 100% off, duration "forever", restricted to the Ultra product
 *   - a promotion code (default VALETVIP) on that coupon
 *
 * The real Stripe creds live in Vercel, not in the repo, so run this once with
 * them in the environment. From product-site/ (so `stripe` resolves):
 *
 *   STRIPE_SECRET_KEY=sk_live_xxx \
 *   STRIPE_PRICE_ID_ULTRA=price_xxx \
 *   COMP_CODE=VALETVIP \
 *   node scripts/create-comp-promo.mjs
 *
 * Optional env:
 *   COMP_COUPON_NAME      label for the coupon (default "VALET VIP — comp Ultra")
 *   COMP_MAX_REDEMPTIONS  cap total redemptions of the code (default: unlimited)
 *
 * It prints the promotion code (store + share with insiders) and the
 * STRIPE_COMP_COUPON_ID line to paste into Vercel — that flips /vip into a
 * one-click, no-typing, no-card flow. Safe to re-run: an existing coupon (same
 * name) and code are reused, not duplicated.
 */
import Stripe from "stripe";

const secret = process.env.STRIPE_SECRET_KEY;
const ultraPrice = process.env.STRIPE_PRICE_ID_ULTRA;
const code = (process.env.COMP_CODE || "VALETVIP").toUpperCase();
const couponName = process.env.COMP_COUPON_NAME || "VALET VIP — comp Ultra";
const maxRedemptions = process.env.COMP_MAX_REDEMPTIONS
  ? Number(process.env.COMP_MAX_REDEMPTIONS)
  : undefined;

if (!secret) {
  console.error("ERROR: set STRIPE_SECRET_KEY (the real key from Vercel).");
  process.exit(1);
}
if (!ultraPrice) {
  console.error("ERROR: set STRIPE_PRICE_ID_ULTRA.");
  process.exit(1);
}

const stripe = new Stripe(secret);
const mode = secret.startsWith("sk_live_") ? "LIVE" : "TEST";

async function main() {
  console.log(`Stripe mode: ${mode}`);

  // Resolve the Ultra product so the coupon applies only to Ultra.
  const price = await stripe.prices.retrieve(ultraPrice);
  const productId =
    typeof price.product === "string" ? price.product : price.product.id;
  console.log(`Ultra product: ${productId}`);

  // Reuse an existing matching coupon if present (idempotent-ish).
  const existingCoupon = (await stripe.coupons.list({ limit: 100 })).data.find(
    (c) => c.name === couponName && c.percent_off === 100 && c.valid,
  );
  const coupon =
    existingCoupon ??
    (await stripe.coupons.create({
      name: couponName,
      percent_off: 100,
      duration: "forever",
      applies_to: { products: [productId] },
    }));
  console.log(`${existingCoupon ? "Reusing" : "Created"} coupon ${coupon.id}`);

  // Reuse an existing promotion code with the same code string if present.
  const existingPromo = (await stripe.promotionCodes.list({ code, limit: 1 }))
    .data[0];
  const promo =
    existingPromo ??
    (await stripe.promotionCodes.create({
      coupon: coupon.id,
      code,
      ...(maxRedemptions ? { max_redemptions: maxRedemptions } : {}),
    }));
  console.log(
    `${existingPromo ? "Reusing" : "Created"} promotion code ${promo.code} (${promo.id})`,
  );

  console.log("\n=== STORE + SHARE (give this to insiders) ===");
  console.log(`Mode:           ${mode}`);
  console.log(`Promotion code: ${promo.code}`);
  console.log(`Promo code id:  ${promo.id}`);
  console.log(`Coupon id:      ${coupon.id}`);
  console.log("\n=== SET IN VERCEL (enables one-click /vip no-card flow) ===");
  console.log(`STRIPE_COMP_COUPON_ID=${coupon.id}`);
  console.log(
    `\nInsiders can then either visit /vip (one click) or enter "${promo.code}" at checkout.`,
  );
}

main().catch((e) => {
  console.error(e?.message || e);
  process.exit(1);
});
