import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";

export const runtime = "nodejs";

/**
 * Public waitlist capture. Anyone may POST { email, source? }; we insert one row
 * per address into the waitlist table (see migration_waitlist.sql) using the
 * service-role client. A re-submit of the same email is a no-op success — the
 * unique index on lower(email) raises 23505, which we swallow so the form always
 * reads as "you're on the list".
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(req: NextRequest) {
  let email = "";
  let source: string | null = null;
  try {
    const body = await req.json();
    email = String(body?.email ?? "").trim().toLowerCase();
    if (body?.source != null) source = String(body.source).slice(0, 60);
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }

  if (!email || email.length > 200 || !EMAIL_RE.test(email)) {
    return NextResponse.json(
      { error: "Enter a valid email address." },
      { status: 400 },
    );
  }

  try {
    const supabase = getSupabaseAdmin();
    const { error } = await supabase
      .from("waitlist")
      .insert({ email, source });
    // 23505 = unique violation: already on the list. Treat as success.
    if (error && error.code !== "23505") {
      console.error("waitlist insert failed:", error.message);
      return NextResponse.json(
        { error: "Could not join the waitlist. Try again." },
        { status: 500 },
      );
    }
  } catch (err) {
    console.error(
      "waitlist route failed:",
      err instanceof Error ? err.message : err,
    );
    return NextResponse.json(
      { error: "Could not join the waitlist. Try again." },
      { status: 500 },
    );
  }

  return NextResponse.json({ ok: true });
}
