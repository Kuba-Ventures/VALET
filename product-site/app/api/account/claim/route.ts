import { NextResponse } from "next/server";
import { createSupabaseServerClient } from "@/lib/auth/server";
import { claimLicenseByKey } from "@/lib/account";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * POST /api/account/claim  { license_key }
 * Links a license key to the signed-in account. Requires an authenticated
 * session; the user id is taken from the verified session, never the request.
 */
export async function POST(req: Request) {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  let key = "";
  try {
    const body = await req.json();
    key = typeof body?.license_key === "string" ? body.license_key.trim() : "";
  } catch {
    key = "";
  }
  if (!key) {
    return NextResponse.json({ error: "Enter a license key." }, { status: 400 });
  }

  const result = await claimLicenseByKey(user.id, key);
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: result.status });
  }
  return NextResponse.json({ ok: true });
}
