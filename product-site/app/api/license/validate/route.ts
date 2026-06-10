import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin, type LicenseStatus } from "@/lib/supabase";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The single, stable integration point for the local app.
 *
 * Request:  { "license_key": "PRODUCT-...." }
 * Response: { "status": "active" | "trialing" | "past_due" | "canceled" | "invalid",
 *             "plan": string | null,
 *             "current_period_end": string | null }
 *
 * An unknown or malformed key returns status "invalid" with HTTP 200, so the
 * app has one code path to handle. Keep this contract small and stable.
 */
export async function POST(req: NextRequest) {
  let licenseKey = "";
  try {
    const body = await req.json();
    licenseKey = typeof body?.license_key === "string" ? body.license_key.trim() : "";
  } catch {
    licenseKey = "";
  }

  const invalid = {
    status: "invalid" as LicenseStatus,
    plan: null,
    current_period_end: null,
  };

  if (!licenseKey) {
    return NextResponse.json(invalid);
  }

  let supabase;
  try {
    supabase = getSupabaseAdmin();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Store unavailable.";
    return NextResponse.json({ error: message }, { status: 500 });
  }

  const { data, error } = await supabase
    .from("licenses")
    .select("status, plan, current_period_end")
    .eq("license_key", licenseKey)
    .maybeSingle();

  if (error) {
    console.error("validate query error:", error.message);
    return NextResponse.json({ error: "Lookup failed." }, { status: 500 });
  }

  if (!data) {
    return NextResponse.json(invalid);
  }

  // Best-effort touch of last_validated_at; ignore failures.
  supabase
    .from("licenses")
    .update({ last_validated_at: new Date().toISOString() })
    .eq("license_key", licenseKey)
    .then(undefined, () => {});

  return NextResponse.json({
    status: data.status as LicenseStatus,
    plan: data.plan ?? null,
    current_period_end: data.current_period_end ?? null,
  });
}
