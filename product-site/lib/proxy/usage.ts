import { getSupabaseAdmin } from "@/lib/supabase";

/**
 * Fair-use metering against the license_usage table (see migration_usage.sql).
 *
 * Allowance is measured in estimated USD cost per rolling monthly window. The
 * launch behavior is SOFT-WARN: we never block or throttle. Past the ceiling the
 * usage status simply reports over_allowance so the app can show an honest
 * banner, and metering data informs future tiers.
 */

const PERIOD_DAYS = 30;

function allowanceUsd(): number {
  const v = Number(process.env.FAIR_USE_MONTHLY_USD ?? "8");
  return Number.isFinite(v) ? v : 8;
}

export interface UsageStatus {
  period_start: string | null;
  period_days: number;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  allowance_usd: number;
  remaining_usd: number;
  over_allowance: boolean;
}

interface UsageRow {
  period_start: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
}

/** Record one call's usage atomically (lazy monthly reset happens in SQL). */
export async function recordUsage(args: {
  licenseKey: string;
  requests?: number;
  inputTokens?: number;
  outputTokens?: number;
  costUsd: number;
}): Promise<void> {
  try {
    const supabase = getSupabaseAdmin();
    await supabase.rpc("record_usage", {
      p_license_key: args.licenseKey,
      p_requests: args.requests ?? 1,
      p_input: Math.round(args.inputTokens ?? 0),
      p_output: Math.round(args.outputTokens ?? 0),
      p_cost: Number(args.costUsd.toFixed(6)),
      p_period_days: PERIOD_DAYS,
    });
  } catch (err) {
    // Metering must never break a paid call. Log and move on.
    console.error("recordUsage failed:", err instanceof Error ? err.message : err);
  }
}

/** Read current allowance status for a license. */
export async function getUsageStatus(licenseKey: string): Promise<UsageStatus> {
  const allowance = allowanceUsd();
  const empty: UsageStatus = {
    period_start: null,
    period_days: PERIOD_DAYS,
    requests: 0,
    input_tokens: 0,
    output_tokens: 0,
    estimated_cost_usd: 0,
    allowance_usd: allowance,
    remaining_usd: allowance,
    over_allowance: false,
  };

  let row: UsageRow | null = null;
  try {
    const supabase = getSupabaseAdmin();
    const { data } = await supabase
      .from("license_usage")
      .select("period_start, requests, input_tokens, output_tokens, estimated_cost_usd")
      .eq("license_key", licenseKey)
      .maybeSingle();
    row = (data as UsageRow) ?? null;
  } catch (err) {
    console.error("getUsageStatus failed:", err instanceof Error ? err.message : err);
    return empty;
  }

  if (!row) return empty;

  // The DB resets lazily on write. On read, if the window has already lapsed,
  // present zeros so the app sees a fresh period even before the next call lands.
  const periodStart = new Date(row.period_start).getTime();
  const expired = Date.now() - periodStart >= PERIOD_DAYS * 86_400_000;
  if (expired) return empty;

  const cost = Number(row.estimated_cost_usd) || 0;
  return {
    period_start: row.period_start,
    period_days: PERIOD_DAYS,
    requests: Number(row.requests) || 0,
    input_tokens: Number(row.input_tokens) || 0,
    output_tokens: Number(row.output_tokens) || 0,
    estimated_cost_usd: cost,
    allowance_usd: allowance,
    remaining_usd: allowance - cost,
    over_allowance: cost >= allowance,
  };
}
