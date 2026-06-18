"use client";

import { useState } from "react";

/**
 * Public early-access capture. Posts the email to /api/waitlist (which is
 * idempotent per address) and shows a confirmation state on success.
 */
export default function WaitlistForm() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, source: "waitlist_page" }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.error ?? "Something went wrong.");
      }
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="panel w-full max-w-md p-8 shadow-glow-lg">
        <div className="label-mono mb-3 text-accent/80">You&apos;re on the list</div>
        <h1 className="text-2xl font-bold tracking-tight">See you soon.</h1>
        <p className="mt-3 leading-relaxed text-ink-dim">
          We&apos;ll email <span className="text-ink">{email}</span> the moment
          your invitation is ready.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="panel w-full max-w-md p-8 shadow-glow-lg">
      <div className="label-mono mb-3 text-accent/80">Early access</div>
      <h1 className="text-2xl font-bold tracking-tight">Join the waitlist</h1>
      <p className="mt-3 leading-relaxed text-ink-dim">
        VALET is rolling out in batches. Leave your email and we&apos;ll be in
        touch when a spot opens.
      </p>

      <label htmlFor="waitlist-email" className="label-mono mt-6 block">
        Email
      </label>
      <input
        id="waitlist-email"
        type="email"
        required
        autoComplete="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@example.com"
        className="input-field mt-2"
      />

      {error && <p className="mt-3 text-sm text-[#ff6b6b]">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="btn-primary mt-6 w-full disabled:opacity-60"
      >
        {loading ? "Joining…" : "Join the waitlist"}
      </button>
    </form>
  );
}
