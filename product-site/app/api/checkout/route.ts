import { NextRequest, NextResponse } from "next/server";
import { stripe } from "@/lib/stripe";

export const runtime = "nodejs";

/**
 * Creates a Stripe Checkout Session for a paid plan and returns its redirect
 * URL. The browser then navigates to Stripe-hosted checkout.
 *
 * Plans:
 *  - "pro"   -> STRIPE_PRICE_ID_PRO      ($20/mo)
 *  - "ultra" -> STRIPE_PRICE_ID_ULTRA   ($50/mo)
 * The free tier never reaches this route (no Stripe subscription).
 *
 * Trial + card up front:
 *  - subscription_data.trial_period_days = 7  (verified against live Stripe docs)
 *  - payment_method_collection = "always"     (default in subscription mode;
 *    set explicitly to collect the card during the trial)
 */
const PRICE_ENV: Record<string, string> = {
  pro: "STRIPE_PRICE_ID_PRO",
  ultra: "STRIPE_PRICE_ID_ULTRA",
};

export async function POST(req: NextRequest) {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

  let plan = "pro";
  try {
    const body = await req.json();
    if (body?.plan === "ultra" || body?.plan === "pro") plan = body.plan;
  } catch {
    // No/invalid body: default to pro.
  }

  const envName = PRICE_ENV[plan];
  const priceId = process.env[envName];

  if (!priceId) {
    return NextResponse.json(
      { error: `${envName} is not configured for the ${plan} plan.` },
      { status: 500 },
    );
  }

  try {
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      line_items: [{ price: priceId, quantity: 1 }],
      payment_method_collection: "always",
      subscription_data: {
        trial_period_days: 7,
      },
      allow_promotion_codes: true,
      success_url: `${siteUrl}/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${siteUrl}/?checkout=canceled`,
    });

    return NextResponse.json({ url: session.url });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Checkout failed.";
    console.error("checkout error:", message);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
