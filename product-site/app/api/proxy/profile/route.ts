import { NextRequest, NextResponse } from "next/server";
import { authorizeLicense } from "@/lib/proxy/auth";
import { getProfileByLicenseKey } from "@/lib/account";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * GET /api/proxy/profile
 * Header: X-License-Key: PRODUCT-....
 *
 * Pulls the latest synced profile down by license key so the app can populate
 * Name / Honorific / DOB / Location at launch without re-login. Mirror of the
 * existing /api/proxy/device-settings (settings) and /api/proxy/sync (push-up).
 */
export async function GET(req: NextRequest): Promise<Response> {
  const auth = await authorizeLicense(req);
  if (!auth.ok) return auth.response;
  const profile = await getProfileByLicenseKey(auth.licenseKey);
  return NextResponse.json({ profile });
}
