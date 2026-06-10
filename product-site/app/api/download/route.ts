import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin, type LicenseStatus } from "@/lib/supabase";
import { isEntitled } from "@/lib/license";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The signed VALET installer URL. Set the DOWNLOAD_URL env var (in Vercel) to the
 * notarized .dmg's public URL and this route redirects there; until then it
 * serves a versioned placeholder. Swapping in the real build is one env change —
 * no redeploy of code needed.
 */
const DOWNLOAD_SOURCE: string | null = process.env.DOWNLOAD_URL || null;
const PLACEHOLDER_VERSION = "0.1.0-placeholder";

/**
 * GET /api/download?key=PRODUCT-....
 * Validates the license, then returns the placeholder artifact (or redirects
 * to the real installer once DOWNLOAD_SOURCE is set).
 */
export async function GET(req: NextRequest) {
  const key = req.nextUrl.searchParams.get("key")?.trim() || "";

  if (!key) {
    return NextResponse.json(
      { error: "Missing license key." },
      { status: 400 },
    );
  }

  let status: LicenseStatus = "invalid";
  try {
    const supabase = getSupabaseAdmin();
    const { data } = await supabase
      .from("licenses")
      .select("status")
      .eq("license_key", key)
      .maybeSingle();
    status = (data?.status as LicenseStatus) ?? "invalid";
  } catch (err) {
    const message = err instanceof Error ? err.message : "Store unavailable.";
    return NextResponse.json({ error: message }, { status: 500 });
  }

  if (!isEntitled(status)) {
    return NextResponse.json(
      { error: "An active or trialing license is required to download." },
      { status: 403 },
    );
  }

  // Real installer path: one-line swap above flips this on.
  if (DOWNLOAD_SOURCE) {
    return NextResponse.redirect(DOWNLOAD_SOURCE);
  }

  // Placeholder: a short README the app build will replace later.
  const readme = [
    "VALET",
    `Placeholder build ${PLACEHOLDER_VERSION}`,
    "",
    "Thank you. Your license is active.",
    "",
    `License key: ${key}`,
    "",
    "The signed desktop app is not available for download yet. This file is a",
    "holding artifact so the purchase and licensing flow works end to end. When",
    "the real installer ships, this endpoint will serve it automatically with no",
    "change on your side.",
    "",
    "Keep your license key. You will paste it into the app's Settings to unlock it.",
    "",
  ].join("\n");

  return new NextResponse(readme, {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Disposition": `attachment; filename="PRODUCT_NAME-${PLACEHOLDER_VERSION}-README.txt"`,
      "Cache-Control": "no-store",
    },
  });
}
