import { NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";

/**
 * Fair-use metering against the license_usage table (see migration_usage.sql).
 *
 * Allowance is estimated USD cost per rolling monthly window. Behavior at the
 * ceiling is set by FAIR_USE_MODE:
 *   - "warn"     (default) — never block; the app shows an honest banner.
 *   - "throttle" — block only the expensive deep-research path; everything else
 *                  keeps working.
 *   - "block"    — refuse all metered calls until the period resets.
 */

const PERIOD_DAYS = 30;

function allowanceUsd(): number {
  const v = Number(process.env.FAIR_USE_MONTHLY_USD ?? "8");
  return Number.isFinite(v) ? v : 8;
}

function fairUseMode(): "warn" | "throttle" | "block" {
  const m = (process.env.FAIR_USE_MODE ?? "warn").trim().toLowerCase();
  return m === "block" ? "block" : m === "throttle" ? "throttle" : "warn";
}

/**
 * Enforce the ceiling BEFORE forwarding a call. Returns a 429 response when the
 * call should be refused, or null to proceed. `action` is "completion" |
 * "research" | "tts".
 */
export async function enforceAllowance(
  licenseKey: string,
  action: string,
): Promise<NextResponse | null> {
  const mode = fairUseMode();
  if (mode === "warn") return null; // soft launch — never blocks
  const status = await getUsageStatus(licenseKey);
  if (!status.over_allowance) return null;
  if (mode === "throttle" && action !== "research") return null; // throttle = deep research only
  // Alert signal — surfaces in Vercel logs / log drains for over-allowance.
  console.warn(
    `fair_use_block mode=${mode} action=${action} license=${licenseKey} ` +
      `cost=${status.estimated_cost_usd.toFixed(4)} allowance=${status.allowance_usd}`,
  );
  return NextResponse.json(
    {
      error: "fair_use_exceeded",
      message:
        "You've reached this month's fair-use limit, sir. It resets at the start of your next period.",
      usage: status,
    },
    { status: 429, headers: { "retry-after": "3600" } },
  );
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
