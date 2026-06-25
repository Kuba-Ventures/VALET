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
/** Only allow internal absolute paths as a redirect target (no open redirects:
 * reject external URLs and protocol-relative "//evil.com"). */
function safeNext(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
  return value;
}

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const queryNext = safeNext(searchParams.get("next"));

  if (code) {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      // Prefer the explicit `?next=`. It often gets stripped by Supabase's
      // confirmation/redirect-allowlist round-trip (which sent VIP signups to
      // /account instead of /vip), so fall back to the destination we stashed in
      // the user's signup metadata, then to /account.
      let dest = queryNext;
      if (!dest) {
        const {
          data: { user },
        } = await supabase.auth.getUser();
        dest = safeNext(user?.user_metadata?.signup_next as string | undefined);
      }
      return NextResponse.redirect(`${origin}${dest ?? "/account"}`);
    }
    console.error("auth callback exchange failed:", error.message);
  }

  return NextResponse.redirect(`${origin}/account/login?error=link`);
}
