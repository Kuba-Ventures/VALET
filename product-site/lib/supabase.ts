import { createClient, SupabaseClient } from "@supabase/supabase-js";

/**
 * Server-only Supabase client using the service role key. This bypasses RLS
 * and must NEVER be imported into client components. All callers here are API
 * routes (server runtime).
 */
let cached: SupabaseClient | null = null;

export function getSupabaseAdmin(): SupabaseClient {
  if (cached) return cached;

  const url = process.env.SUPABASE_URL;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !serviceKey) {
    throw new Error(
      "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
    );
  }

  cached = createClient(url, serviceKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return cached;
}

export type LicenseStatus =
  | "active"
  | "trialing"
  | "past_due"
  | "canceled"
  | "invalid";

export interface LicenseRow {
  id: string;
  license_key: string;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  status: LicenseStatus;
  plan: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  created_at: string;
  updated_at: string | null;
  last_validated_at: string | null;
}
