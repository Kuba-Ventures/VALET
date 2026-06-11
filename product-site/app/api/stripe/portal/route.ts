import { NextResponse } from "next/server";
import { stripe } from "@/lib/stripe";
import { createSupabaseServerClient } from "@/lib/auth/server";
import { getSupabaseAdmin } from "@/lib/supabase";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * POST /api/stripe/portal
 * Opens a Stripe Billing Portal session for the signed-in user's customer, so
 * they can update card details, change plan, or cancel — all on Stripe-hosted
 * pages. We resolve the customer from the user's most recent license; the user
 * id comes from the verified session.
 */
export async function POST() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const admin = getSupabaseAdmin();
  const { data, error } = await admin
    .from("licenses")
    .select("stripe_customer_id")
    .eq("user_id", user.id)
    .not("stripe_customer_id", "is", null)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) {
    console.error("portal lookup failed:", error.message);
    return NextResponse.json({ error: "Lookup failed." }, { status: 500 });
  }

  const customerId = data?.stripe_customer_id as string | undefined;
  if (!customerId) {
    return NextResponse.json(
      { error: "No billing account is linked to this login yet." },
      { status: 404 },
    );
  }

  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
  try {
    const session = await stripe.billingPortal.sessions.create({
      customer: customerId,
      return_url: `${siteUrl}/account`,
    });
    return NextResponse.json({ url: session.url });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Portal failed.";
    console.error("portal create failed:", message);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
