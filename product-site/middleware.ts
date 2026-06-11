import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

type CookieToSet = { name: string; value: string; options: CookieOptions };

/**
 * Runs on /account/* only. Two jobs:
 *   1. Refresh the Supabase auth cookie so server components see a live session.
 *   2. Gate the dashboard: an unauthenticated (or unverified) visitor to a
 *      protected account page is bounced to /account/login.
 *
 * Email verification is enforced for free by Supabase: until a user confirms
 * their address there is no session, so getUser() returns null and they cannot
 * reach the dashboard. The license itself keeps working in the app regardless.
 */

const AUTH_PAGES = new Set([
  "/account/login",
  "/account/signup",
  "/account/reset",
  "/account/update-password",
]);

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  // If auth isn't configured yet, don't hard-fail the route — just let it
  // through; the page will render its own "not configured" guard.
  if (!url || !anonKey) return response;

  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value),
        );
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isAuthPage = AUTH_PAGES.has(path);

  if (!user && !isAuthPage) {
    const redirect = request.nextUrl.clone();
    redirect.pathname = "/account/login";
    redirect.searchParams.set("next", path);
    return NextResponse.redirect(redirect);
  }

  // Signed-in users have no business on the login/signup screens.
  if (user && (path === "/account/login" || path === "/account/signup")) {
    const redirect = request.nextUrl.clone();
    redirect.pathname = "/account";
    redirect.search = "";
    return NextResponse.redirect(redirect);
  }

  return response;
}

export const config = {
  matcher: ["/account/:path*"],
};
