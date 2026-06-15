import { NextRequest, NextResponse } from "next/server";
import { loginAndProvision } from "@/lib/account";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 10;

/**
 * POST /api/account/app-login   { email, password }
 *
 * Desktop-app login: verifies account credentials and returns what the app needs
 * to provision itself — the license key (preferring an entitled one) and the
 * latest synced profile. Replaces manual license-key entry.
 *
 * The password is used only to authenticate against Supabase here and is never
 * stored or logged. The license key remains the bearer for every other call.
 */
export async function POST(req: NextRequest): Promise<Response> {
  let body: { email?: unknown; password?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (!email || !password) {
    return NextResponse.json({ error: "Email and password are required." }, { status: 400 });
  }

  const result = await loginAndProvision(email, password);
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: result.status });
  }

  return NextResponse.json({
    license_key: result.licenseKey,
    has_license: Boolean(result.licenseKey),
    status: result.status,
    plan: result.planLabel,
    profile: result.profile,
  });
}
