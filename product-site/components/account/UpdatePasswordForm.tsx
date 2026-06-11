"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/auth/client";
import PasswordInput from "@/components/account/PasswordInput";

/**
 * Sets a new password. Reached from the reset email link, which has already
 * established a short-lived session via /auth/callback, so updateUser() applies
 * to the right account.
 */
export default function UpdatePasswordForm() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Those passwords don't match.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const supabase = createSupabaseBrowserClient();
      const { error } = await supabase.auth.updateUser({ password });
      if (error) throw error;
      setDone(true);
      setTimeout(() => {
        router.replace("/account");
        router.refresh();
      }, 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setLoading(false);
    }
  }

  return (
    <div className="panel w-full max-w-md p-8 shadow-glow-lg">
      <div className="label-mono mb-3 text-accent/80">New password</div>
      <h1 className="text-2xl font-bold tracking-tight">Choose a new password</h1>

      {done ? (
        <p className="mt-3 leading-relaxed text-ink-dim">
          Password updated. Taking you to your account...
        </p>
      ) : (
        <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="label-mono">New password</span>
            <PasswordInput
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
              placeholder="At least 8 characters"
              ariaLabel="New password"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="label-mono">Confirm new password</span>
            <PasswordInput
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
              placeholder="Re-enter your new password"
              ariaLabel="Confirm new password"
            />
          </label>

          {error && (
            <span className="text-sm text-[#ff8a8a]" role="alert">
              {error}
            </span>
          )}

          <button type="submit" disabled={loading} className="btn-primary mt-1">
            {loading ? "Saving..." : "Update password"}
          </button>
        </form>
      )}
    </div>
  );
}
