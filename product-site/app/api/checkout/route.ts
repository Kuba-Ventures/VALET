import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";
import { stripe } from "@/lib/stripe";
import { createSupabaseServerClient } from "@/lib/auth/server";

export const runtime = "nodejs";

/**
 * Creates a Stripe Checkout Session and returns its redirect URL. The browser
 * then navigates to Stripe-hosted checkout.
 *
 * Plans:
 *  - "pro"   -> STRIPE_PRICE_ID_PRO      ($20/mo)
 *  - "ultra" -> STRIPE_PRICE_ID_ULTRA   ($50/mo)
 * The free tier never reaches this route (no Stripe subscription).
 *
 * Two modes:
 *  - Normal (paid): 7-day trial, card up front
 *      - subscription_data.trial_period_days = 7
 *      - payment_method_collection = "always" (collect the card during trial)
 *  - Comp (VIP, body { comp: true }): complimentary Ultra with NO card
 *      - always Ultra; no trial
 *      - payment_method_collection = "if_required" — Stripe collects a card
 *        only when money is actually due. With a 100%-off-forever coupon the
 *        total is $0, so no card is taken. WITHOUT the discount the full price
 *        is due and a card IS required, so this path can't self-comp.
 *      - the coupon is auto-applied when STRIPE_COMP_COUPON_ID is set (one-click
 *        /vip flow); otherwise the VIP enters the promotion code by hand.
 */
const PRICE_ENV: Record<string, string> = {
  pro: "STRIPE_PRICE_ID_PRO",
  ultra: "STRIPE_PRICE_ID_ULTRA",
};

export async function POST(req: NextRequest) {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

  let plan = "pro";
  let comp = false;
  try {
    const body = await req.json();
    if (body?.plan === "ultra" || body?.plan === "pro") plan = body.plan;
    comp = body?.comp === true;
  } catch {
    // No/invalid body: default to pro.
  }

  // Comp grants are always Ultra.
  if (comp) plan = "ultra";

  // Identify the signed-in user. Both paid trials and comp (VIP) grants now
  // REQUIRE an account so the license binds to it up front (via the UUID stamped
  // into subscription metadata below) instead of relying on a later email-match.
  // The buyer creates + verifies their account before this route runs; the
  // CheckoutButton routes anonymous visitors to signup first, so a bare 401 here
  // is only ever hit by a direct/stale request.
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json(
      {
        error: comp
          ? "Please create your account or sign in before activating VIP access."
          : "Please create your account or sign in before starting your trial.",
      },
      { status: 401 },
    );
  }

  const envName = PRICE_ENV[plan];
  const priceId = process.env[envName];

  if (!priceId) {
    return NextResponse.json(
      { error: `${envName} is not configured for the ${plan} plan.` },
      { status: 500 },
    );
  }

  const params: Stripe.Checkout.SessionCreateParams = {
    mode: "subscription",
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: `${siteUrl}/success?session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: comp
      ? `${siteUrl}/vip?checkout=canceled`
      : `${siteUrl}/?checkout=canceled`,
  };

  if (comp) {
    params.payment_method_collection = "if_required";
    const couponId = process.env.STRIPE_COMP_COUPON_ID;
    if (couponId) {
      // Auto-apply the comp discount: one click, no code to type.
      params.discounts = [{ coupon: couponId }];
    } else {
      // Fallback until STRIPE_COMP_COUPON_ID is set: VIP enters the code.
      // (discounts and allow_promotion_codes are mutually exclusive in Stripe.)
      params.allow_promotion_codes = true;
    }
  } else {
    params.payment_method_collection = "always";
    params.subscription_data = { trial_period_days: 7 };
    params.allow_promotion_codes = true;
  }

  // Bind the checkout to the account (guaranteed present by the 401 gate above).
  // Prefilling and locking the email keeps the Stripe customer email aligned with
  // the verified account email, and the metadata UUID is what the webhook reads to
  // set licenses.user_id directly — no email-match guesswork.
  if (user) {
    params.customer_email = user.email ?? undefined;
    params.client_reference_id = user.id;
    params.subscription_data = {
      ...(params.subscription_data ?? {}),
      metadata: { valet_user_id: user.id },
    };
  }

  try {
    const session = await stripe.checkout.sessions.create(params);
    return NextResponse.json({ url: session.url });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Checkout failed.";
    console.error("checkout error:", message);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
