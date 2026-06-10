import { randomBytes } from "crypto";
import Stripe from "stripe";
import { getSupabaseAdmin, type LicenseStatus } from "./supabase";

/**
 * Generate an opaque, URL-safe license key. Format: PRODUCT-XXXX-XXXX-XXXX-XXXX
 * using base32-ish alphabet (no ambiguous chars), backed by 20 random bytes.
 */
const ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"; // no I, L, O, 0, 1

export function generateLicenseKey(): string {
  const bytes = randomBytes(20);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += ALPHABET[bytes[i] % ALPHABET.length];
    if ((i + 1) % 4 === 0 && i < bytes.length - 1) out += "-";
  }
  return `PRODUCT-${out}`;
}

/**
 * Map a Stripe subscription status onto our entitlement status. We keep the
 * vocabulary small and stable because the local app depends on it.
 */
export function mapStripeStatus(
  stripeStatus: Stripe.Subscription.Status | string,
): LicenseStatus {
  switch (stripeStatus) {
    case "trialing":
      return "trialing";
    case "active":
      return "active";
    case "past_due":
    case "unpaid":
      return "past_due";
    case "canceled":
    case "incomplete_expired":
      return "canceled";
    default:
      // incomplete, paused, or anything unexpected: treat as not entitled.
      return "invalid";
  }
}

/** Statuses that grant access to the app. */
export function isEntitled(status: LicenseStatus): boolean {
  return status === "active" || status === "trialing";
}

function toIso(unixSeconds: number | null | undefined): string | null {
  if (!unixSeconds) return null;
  return new Date(unixSeconds * 1000).toISOString();
}

/**
 * Stripe moved billing period fields onto subscription items in recent API
 * versions while older versions keep them on the subscription. Read both so we
 * stay correct regardless of the pinned version.
 */
function periodEnd(sub: Stripe.Subscription): number | null {
  const top = (sub as unknown as { current_period_end?: number })
    .current_period_end;
  if (top) return top;
  const item = sub.items?.data?.[0] as unknown as { current_period_end?: number };
  return item?.current_period_end ?? null;
}

/**
 * Upsert a license row from a subscription and return its license key. Keyed on
 * stripe_subscription_id so lifecycle updates (trial -> active -> canceled)
 * land on the same row. The license key is generated once, on first insert, and
 * never rotated. Idempotent: safe to call from both the webhook and the success
 * page (which may run before the webhook lands).
 */
export async function upsertLicenseFromSubscription(
  sub: Stripe.Subscription,
): Promise<string> {
  const supabase = getSupabaseAdmin();
  const customerId =
    typeof sub.customer === "string" ? sub.customer : sub.customer.id;
  const planId = sub.items?.data?.[0]?.price?.id ?? null;
  const status = mapStripeStatus(sub.status);

  const { data: existing } = await supabase
    .from("licenses")
    .select("id, license_key")
    .eq("stripe_subscription_id", sub.id)
    .maybeSingle();

  const base = {
    stripe_customer_id: customerId,
    stripe_subscription_id: sub.id,
    status,
    plan: planId,
    trial_ends_at: toIso(sub.trial_end),
    current_period_end: toIso(periodEnd(sub)),
    updated_at: new Date().toISOString(),
  };

  if (existing) {
    await supabase.from("licenses").update(base).eq("id", existing.id);
    return existing.license_key as string;
  }

  const license_key = generateLicenseKey();
  await supabase.from("licenses").insert({
    license_key,
    created_at: new Date().toISOString(),
    ...base,
  });
  return license_key;
}
