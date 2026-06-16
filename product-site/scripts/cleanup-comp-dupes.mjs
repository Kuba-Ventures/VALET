#!/usr/bin/env node
/**
 * Collapse duplicate VALET subscriptions for one email down to a single
 * comp (VIP, free-for-life) Ultra. Each checkout that ran without an existing
 * customer created a NEW Stripe customer + subscription, so testing the comp
 * flow a few times leaves several live subscriptions (and several license rows)
 * on the same email.
 *
 * This script, across every customer with the target email:
 *   - keeps ONE entitled (active/trialing) comp subscription, the newest;
 *   - cancels every other entitled subscription (extra comps, trials, paid);
 *   - never touches already-canceled subscriptions.
 *
 * DRY-RUN by default: it only prints the plan. Set APPLY=1 to actually cancel.
 *
 *   STRIPE_SECRET_KEY=sk_live_or_rk_live_xxx \
 *   TARGET_EMAIL=you@example.com \
 *   node scripts/cleanup-comp-dupes.mjs            # dry-run
 *
 *   ... APPLY=1 node scripts/cleanup-comp-dupes.mjs   # actually cancels
 *
 * Canceling a subscription flips its license to "canceled" (via webhook), so
 * the extra entitlements drop off. The canceled rows still render as "Canceled"
 * cards on the account page until separately pruned.
 */
import Stripe from "stripe";

const secret = process.env.STRIPE_SECRET_KEY;
const email = process.env.TARGET_EMAIL;
const apply = process.env.APPLY === "1";

if (!secret) {
  console.error("ERROR: set STRIPE_SECRET_KEY.");
  process.exit(1);
}
if (!email) {
  console.error("ERROR: set TARGET_EMAIL (the account to clean up).");
  process.exit(1);
}

const stripe = new Stripe(secret);
const mode = secret.includes("_live_") ? "LIVE" : "TEST";

function isComp(sub) {
  const coupons = [];
  for (const d of sub.discounts ?? []) {
    if (d && typeof d === "object" && d.coupon) coupons.push(d.coupon);
  }
  if (sub.discount?.coupon) coupons.push(sub.discount.coupon);
  return coupons.some((c) => c.percent_off === 100 && c.duration === "forever");
}

const ENTITLED = new Set(["active", "trialing"]);

async function main() {
  console.log(`Stripe mode: ${mode}`);
  console.log(`Target email: ${email}`);

  const customers = (await stripe.customers.list({ email, limit: 100 })).data;
  if (!customers.length) {
    console.log("No customers found for that email. Nothing to do.");
    return;
  }
  console.log(`Found ${customers.length} customer(s) for this email.`);

  const subs = [];
  for (const customer of customers) {
    const list = await stripe.subscriptions.list({
      customer: customer.id,
      status: "all",
      limit: 100,
      expand: ["data.discounts"],
    });
    for (const sub of list.data) {
      subs.push({
        id: sub.id,
        customer: customer.id,
        status: sub.status,
        created: sub.created,
        comp: isComp(sub),
        entitled: ENTITLED.has(sub.status),
      });
    }
  }

  if (!subs.length) {
    console.log("No subscriptions found. Nothing to do.");
    return;
  }

  const entitled = subs.filter((s) => s.entitled);
  const comps = entitled
    .filter((s) => s.comp)
    .sort((a, b) => b.created - a.created); // newest first
  const keeper = comps[0] ?? null;

  console.log(`\nAll subscriptions (${subs.length}):`);
  for (const s of subs) {
    const tag = s.id === keeper?.id ? "KEEP" : s.entitled ? "CANCEL" : "skip";
    const kind = s.comp ? "comp" : "paid/trial";
    console.log(`  [${tag.padEnd(6)}] ${s.id}  ${s.status.padEnd(9)} ${kind}`);
  }

  if (!keeper) {
    console.log(
      "\n⚠️  No entitled COMP subscription found to keep. Refusing to cancel " +
        "everything. Create/redeem the comp first, then re-run.",
    );
    return;
  }

  const toCancel = entitled.filter((s) => s.id !== keeper.id);
  console.log(
    `\nPlan: keep ${keeper.id} (comp), cancel ${toCancel.length} other ` +
      `entitled subscription(s).`,
  );

  if (!toCancel.length) {
    console.log("Already down to a single comp subscription. Done.");
    return;
  }

  if (!apply) {
    console.log("\nDRY-RUN. Re-run with APPLY=1 to cancel the above.");
    return;
  }

  for (const s of toCancel) {
    await stripe.subscriptions.cancel(s.id);
    console.log(`Canceled ${s.id}`);
  }
  console.log(`\nDone. Kept ${keeper.id}, canceled ${toCancel.length}.`);
}

main().catch((e) => {
  console.error(e?.message || e);
  process.exit(1);
});
