import { NextResponse } from "next/server";
import { stripe } from "@/lib/stripe";

export const runtime = "nodejs";

/**
 * Creates a Stripe Checkout Session for the single subscription plan and
 * returns its redirect URL. The browser then navigates to Stripe-hosted
 * checkout.
 *
 * Trial + card up front:
 *  - subscription_data.trial_period_days = 7  (verified against live Stripe docs)
 *  - payment_method_collection = "always"     (default in subscription mode;
 *    set explicitly to collect the card during the trial)
 */
export async function POST() {
  const priceId = process.env.STRIPE_PRICE_ID;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

  if (!priceId) {
    return NextResponse.json(
      { error: "STRIPE_PRICE_ID is not configured." },
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
