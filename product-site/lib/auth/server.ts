import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";

type CookieToSet = { name: string; value: string; options: CookieOptions };

/**
 * Supabase client bound to the request's auth cookies, for use inside Server
 * Components and Route Handlers. Uses the public anon key + the signed-in user's
 * session (not the service role), so it only ever sees what that user is allowed
 * to see at the auth layer. Data reads that must cross RLS still go through the
 * service-role admin client (lib/supabase.ts), always scoped by the verified
 * user id from `auth.getUser()`.
 */
export async function createSupabaseServerClient() {
  const cookieStore = await cookies();
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    throw new Error(
      "Auth is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.",
    );
  }

  return createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set({ name, value, ...options }),
          );
        } catch {
          // setAll was called from a Server Component, where cookies are
          // read-only. The middleware refreshes the session cookie instead, so
          // this is safe to ignore.
        }
      },
    },
  });
}
