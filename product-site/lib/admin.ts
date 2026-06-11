import { getSupabaseAdmin, type LicenseStatus } from "./supabase";
import { getUsageStatus, type UsageStatus } from "./proxy/usage";
import { planLabel } from "./account";

/**
 * Owner-only admin layer. Membership is an explicit allow-list of emails in the
 * ADMIN_EMAILS env var (comma-separated). The check runs against the verified
 * session email, never anything from the request. There is no self-service
 * route to this data — only listed emails see it.
 */

export function isAdmin(email: string | null | undefined): boolean {
  if (!email) return false;
  const allow = (process.env.ADMIN_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  return allow.includes(email.toLowerCase());
}

/** Rough monthly value of a tier, for an MRR estimate. */
export function priceForPlan(label: string): number {
  if (label === "Ultra") return 50;
  if (label === "Pro") return 20;
  return 0;
}

export interface AdminAccount {
  customerEmail: string | null;
  planLabel: string;
  status: LicenseStatus;
  currentPeriodEnd: string | null;
  trialEndsAt: string | null;
  createdAt: string;
  claimed: boolean;
  usage: UsageStatus;
}

/**
 * Every license in the store, newest first, each with its current usage. Reads
 * with the service role — this is the one place that intentionally crosses all
 * users, and it's reachable only after an isAdmin() gate.
 */
export async function getAllAccounts(): Promise<AdminAccount[]> {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("licenses")
    .select(
      "license_key, customer_email, status, plan, current_period_end, trial_ends_at, created_at, user_id",
    )
    .order("created_at", { ascending: false });

  if (error) {
    console.error("getAllAccounts failed:", error.message);
    return [];
  }

  const rows = data ?? [];
  return Promise.all(
    rows.map(async (row) => ({
      customerEmail: (row.customer_email as string | null) ?? null,
      planLabel: planLabel((row.plan as string | null) ?? null),
      status: row.status as LicenseStatus,
      currentPeriodEnd: (row.current_period_end as string | null) ?? null,
      trialEndsAt: (row.trial_ends_at as string | null) ?? null,
      createdAt: row.created_at as string,
      claimed: Boolean(row.user_id),
      usage: await getUsageStatus(
        row.license_key as string,
        (row.plan as string | null) ?? null,
      ),
    })),
  );
}
