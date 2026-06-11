"use client";

import Link from "next/link";
import { useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/auth/client";

/**
 * Requests a password-reset email. Supabase sends a link back to
 * /auth/callback?next=/account/update-password, where the user sets a new one.
 */
export default function ResetForm() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const supabase = createSupabaseBrowserClient();
      const redirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent("/account/update-password")}`;
      const { error } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo,
      });
      if (error) throw error;
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  if (sent) {
    return (
      <div className="panel w-full max-w-md p-8 shadow-glow-lg">
        <div className="label-mono mb-3 text-accent/80">Check your email</div>
        <h1 className="text-2xl font-bold tracking-tight">Reset link sent</h1>
        <p className="mt-3 leading-relaxed text-ink-dim">
          If an account exists for{" "}
          <span className="text-ink">{email}</span>, a password-reset link is on
          its way.
        </p>
        <Link href="/account/login" className="btn-ghost mt-6">
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="panel w-full max-w-md p-8 shadow-glow-lg">
      <div className="label-mono mb-3 text-accent/80">Reset password</div>
      <h1 className="text-2xl font-bold tracking-tight">Forgot your password?</h1>
      <p className="mt-2 text-sm text-ink-dim">
        Enter your email and we will send a reset link.
      </p>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="label-mono">Email</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input-field"
            placeholder="you@example.com"
          />
        </label>

        {error && (
          <span className="text-sm text-[#ff8a8a]" role="alert">
            {error}
          </span>
        )}

        <button type="submit" disabled={loading} className="btn-primary mt-1">
          {loading ? "Sending..." : "Send reset link"}
        </button>
      </form>

      <Link
        href="/account/login"
        className="mt-6 block text-sm text-ink-dim hover:text-accent"
      >
        Back to sign in
      </Link>
    </div>
  );
}
