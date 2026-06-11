import { createBrowserClient } from "@supabase/ssr";

/**
 * Supabase client for use in Client Components (login / signup / reset forms).
 * Public anon key only — never the service role. Auth state is persisted in
 * cookies that the middleware and server client read back.
 */
export function createSupabaseBrowserClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    throw new Error(
      "Auth is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.",
    );
  }

  return createBrowserClient(url, anonKey);
}
