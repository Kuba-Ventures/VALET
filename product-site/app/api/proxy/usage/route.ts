import { NextRequest, NextResponse } from "next/server";
import { authorizeLicense } from "@/lib/proxy/auth";
import { getUsageStatus } from "@/lib/proxy/usage";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 10;

/**
 * GET /api/proxy/usage
 * Header:  X-License-Key: PRODUCT-....
 * Returns the license's remaining fair-use allowance for the current period so
 * the app can display it. Launch behavior is soft-warn: over_allowance may be
 * true while calls still succeed.
 */
export async function GET(req: NextRequest) {
  const auth = await authorizeLicense(req);
  if (!auth.ok) return auth.response;

  const status = await getUsageStatus(auth.licenseKey);
  return NextResponse.json({ status: auth.status, usage: status });
}
