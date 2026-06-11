import { getSupabaseAdmin, type LicenseRow } from "./supabase";
import { getUsageStatus, type UsageStatus } from "./proxy/usage";

/**
 * Account data layer. Everything here runs server-side with the service-role
 * client and is ALWAYS scoped by a user id that the caller has already verified
 * via `supabase.auth.getUser()`. Never pass an unverified id in.
 */

export interface AccountLicense {
  licenseKey: string;
  status: LicenseRow["status"];
  planLabel: string;
  currentPeriodEnd: string | null;
  trialEndsAt: string | null;
  hasBilling: boolean;
  usage: UsageStatus;
}

/** Map a stored Stripe price id onto a human plan label. */
export function planLabel(priceId: string | null): string {
  if (priceId && priceId === process.env.STRIPE_PRICE_ID_ULTRA) return "Ultra";
  if (priceId && priceId === process.env.STRIPE_PRICE_ID_PRO) return "Pro";
  return "Trial";
}

/**
 * Auto-claim: attach every still-unclaimed license bought with this email to
 * this user. Idempotent — safe to call on each dashboard load. Returns the
 * number of licenses newly linked.
 */
export async function linkLicensesByEmail(
  userId: string,
  email: string | null | undefined,
): Promise<number> {
  if (!email) return 0;
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("licenses")
    .update({ user_id: userId })
    .ilike("customer_email", email)
    .is("user_id", null)
    .select("id");
  if (error) {
    console.error("linkLicensesByEmail failed:", error.message);
    return 0;
  }
  return data?.length ?? 0;
}

export type ClaimResult =
  | { ok: true }
  | { ok: false; status: number; error: string };

/**
 * Manual claim by license key. Succeeds if the key exists and is either
 * unclaimed or already owned by this user. The license key is the app's bearer
 * credential, so possession is sufficient proof — but a key already bound to a
 * different account is refused.
 */
export async function claimLicenseByKey(
  userId: string,
  licenseKey: string,
): Promise<ClaimResult> {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("licenses")
    .select("id, user_id")
    .eq("license_key", licenseKey)
    .maybeSingle();

  if (error) {
    console.error("claimLicenseByKey lookup failed:", error.message);
    return { ok: false, status: 500, error: "Lookup failed. Try again." };
  }
  if (!data) {
    return { ok: false, status: 404, error: "No license found for that key." };
  }
  if (data.user_id && data.user_id !== userId) {
    return {
      ok: false,
      status: 409,
      error: "That key is already linked to another account.",
    };
  }
  if (data.user_id === userId) {
    return { ok: true }; // already linked to this user
  }

  const { error: updErr } = await supabase
    .from("licenses")
    .update({ user_id: userId })
    .eq("id", data.id)
    .is("user_id", null); // guard against a race claiming it first
  if (updErr) {
    console.error("claimLicenseByKey update failed:", updErr.message);
    return { ok: false, status: 500, error: "Could not link the key. Try again." };
  }
  return { ok: true };
}

/** Load every license this user owns, each with its current usage status. */
export async function getAccountLicenses(
  userId: string,
): Promise<AccountLicense[]> {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("licenses")
    .select(
      "license_key, status, plan, current_period_end, trial_ends_at, stripe_customer_id",
    )
    .eq("user_id", userId)
    .order("created_at", { ascending: false });

  if (error) {
    console.error("getAccountLicenses failed:", error.message);
    return [];
  }

  const rows = data ?? [];
  return Promise.all(
    rows.map(async (row) => ({
      licenseKey: row.license_key as string,
      status: row.status as LicenseRow["status"],
      planLabel: planLabel((row.plan as string | null) ?? null),
      currentPeriodEnd: (row.current_period_end as string | null) ?? null,
      trialEndsAt: (row.trial_ends_at as string | null) ?? null,
      hasBilling: Boolean(row.stripe_customer_id),
      usage: await getUsageStatus(
        row.license_key as string,
        (row.plan as string | null) ?? null,
      ),
    })),
  );
}

/** Snapshot the desktop app pushes to /api/proxy/sync (see migration_sync.sql). */
export interface AccountSync {
  profile: {
    name?: string;
    honorific?: string;
    date_of_birth?: string;
    location?: string;
    work_email?: string;
    personal_email?: string;
  } | null;
  stats: {
    total_tasks?: number;
    success_rate?: number;
    avg_duration_seconds?: number;
    top_actions?: { action: string; count: number }[];
  } | null;
  connections: { calendar?: boolean; mail?: boolean; notes?: boolean } | null;
  appVersion: string | null;
  updatedAt: string;
}

/**
 * Most recent sync snapshot across this user's licenses (a user with one app
 * install has one). Returns null until the desktop app has synced at least once.
 */
export async function getLatestAccountSync(
  userId: string,
): Promise<AccountSync | null> {
  const supabase = getSupabaseAdmin();
  const { data: lic } = await supabase
    .from("licenses")
    .select("license_key")
    .eq("user_id", userId);
  const keys = (lic ?? []).map((r) => r.license_key as string);
  if (!keys.length) return null;

  const { data, error } = await supabase
    .from("account_sync")
    .select("profile, stats, connections, app_version, updated_at")
    .in("license_key", keys)
    .order("updated_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) {
    console.error("getLatestAccountSync failed:", error.message);
    return null;
  }
  if (!data) return null;

  return {
    profile: (data.profile as AccountSync["profile"]) ?? null,
    stats: (data.stats as AccountSync["stats"]) ?? null,
    connections: (data.connections as AccountSync["connections"]) ?? null,
    appVersion: (data.app_version as string | null) ?? null,
    updatedAt: data.updated_at as string,
  };
}
