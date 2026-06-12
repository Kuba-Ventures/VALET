import { NextResponse } from "next/server";
import { createSupabaseServerClient } from "@/lib/auth/server";
import {
  getDeviceSettingsForUser,
  setDeviceSettingsForUser,
  sanitizeDeviceSettings,
} from "@/lib/device-settings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Web-facing device settings for the signed-in user.
 *   GET  → the user's current settings.
 *   POST { settings } → save settings to all of the user's licenses (the app
 *          reads them via /api/proxy/device-settings).
 * The user id always comes from the verified session, never the request.
 */
export async function GET() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }
  const settings = await getDeviceSettingsForUser(user.id);
  return NextResponse.json({ settings });
}

export async function POST(req: Request) {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  let body: Record<string, unknown> = {};
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  // Accept either { settings: {...} } or a bare {...}.
  const settings = sanitizeDeviceSettings(body.settings ?? body);
  const ok = await setDeviceSettingsForUser(user.id, settings);
  if (!ok) {
    return NextResponse.json(
      { error: "No license is linked to this account yet." },
      { status: 404 },
    );
  }
  return NextResponse.json({ ok: true, settings });
}
