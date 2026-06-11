"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/auth/client";

/**
 * Email + password auth, used for both login and signup (mode prop). On signup
 * Supabase sends a confirmation email; until the user clicks it they have no
 * session and cannot reach the dashboard — that is our email-verification gate.
 */
export default function AuthForm({
  mode,
  next = "/account",
}: {
  mode: "login" | "signup";
  next?: string;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const isSignup = mode === "signup";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const supabase = createSupabaseBrowserClient();

    try {
      if (isSignup) {
        const emailRedirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo },
        });
        if (error) throw error;
        // With email confirmation on, there is no session yet.
        if (!data.session) {
          setSent(true);
          setLoading(false);
          return;
        }
        router.replace(next);
        router.refresh();
      } else {
        const { error } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (error) throw error;
        router.replace(next);
        router.refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setLoading(false);
    }
  }

  if (sent) {
    return (
      <div className="panel w-full max-w-md p-8 shadow-glow-lg">
        <div className="label-mono mb-3 text-accent/80">Check your email</div>
        <h1 className="text-2xl font-bold tracking-tight">Confirm your address</h1>
        <p className="mt-3 leading-relaxed text-ink-dim">
          We sent a confirmation link to{" "}
          <span className="text-ink">{email}</span>. Click it to finish creating
          your account, then sign in.
        </p>
        <Link href="/account/login" className="btn-ghost mt-6">
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="panel w-full max-w-md p-8 shadow-glow-lg">
      <div className="label-mono mb-3 text-accent/80">
        {isSignup ? "Create account" : "Sign in"}
      </div>
      <h1 className="text-2xl font-bold tracking-tight">
        {isSignup ? "Your VALET account" : "Welcome back"}
      </h1>
      <p className="mt-2 text-sm text-ink-dim">
        {isSignup
          ? "One place for your license, usage and billing."
          : "License, usage and billing in one place."}
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
        <label className="flex flex-col gap-1.5">
          <span className="label-mono">Password</span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete={isSignup ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input-field"
            placeholder={isSignup ? "At least 8 characters" : "Your password"}
          />
        </label>

        {error && (
          <span className="text-sm text-[#ff8a8a]" role="alert">
            {error}
          </span>
        )}

        <button type="submit" disabled={loading} className="btn-primary mt-1">
          {loading
            ? "One moment..."
            : isSignup
              ? "Create account"
              : "Sign in"}
        </button>
      </form>

      <div className="mt-6 flex items-center justify-between text-sm text-ink-dim">
        {isSignup ? (
          <Link href="/account/login" className="hover:text-accent">
            Have an account? Sign in
          </Link>
        ) : (
          <Link href="/account/signup" className="hover:text-accent">
            Create an account
          </Link>
        )}
        {!isSignup && (
          <Link href="/account/reset" className="hover:text-accent">
            Forgot password?
          </Link>
        )}
      </div>
    </div>
  );
}
