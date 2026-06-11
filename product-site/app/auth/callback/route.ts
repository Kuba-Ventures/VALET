import { NextResponse } from "next/server";
import { createSupabaseServerClient } from "@/lib/auth/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * OAuth/email-confirmation callback. Supabase sends the user here with a `code`
 * after they confirm their email address or follow a password-reset link. We
 * exchange it for a session cookie and forward to the dashboard (or wherever
 * `next` points).
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/account";

  if (code) {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
    console.error("auth callback exchange failed:", error.message);
  }

  return NextResponse.redirect(`${origin}/account/login?error=link`);
}
