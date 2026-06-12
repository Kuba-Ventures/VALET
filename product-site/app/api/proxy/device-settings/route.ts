import { NextRequest, NextResponse } from "next/server";
import { authorizeLicense } from "@/lib/proxy/auth";
import { getDeviceSettings } from "@/lib/device-settings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 10;

/**
 * GET /api/proxy/device-settings
 * Header: X-License-Key: PRODUCT-....
 *
 * The desktop app reads its web-controlled settings here (voice, voice id,
 * telemetry, …) and applies them locally. Authed by the license key, like the
 * other app↔proxy routes. (Phase 3 wires the app to call this.)
 */
export async function GET(req: NextRequest) {
  const auth = await authorizeLicense(req);
  if (!auth.ok) return auth.response;
  const settings = await getDeviceSettings(auth.licenseKey);
  return NextResponse.json({ settings });
}
