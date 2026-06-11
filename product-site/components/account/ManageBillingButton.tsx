"use client";

import { useState } from "react";

/**
 * Opens the Stripe Billing Portal for the signed-in user (card, plan, cancel).
 */
export default function ManageBillingButton() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function openPortal() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/stripe/portal", { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.url) {
        throw new Error(data.error || "Could not open billing.");
      }
      window.location.href = data.url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setLoading(false);
    }
  }

  return (
    <div className="inline-flex flex-col items-start gap-2">
      <button onClick={openPortal} disabled={loading} className="btn-ghost !px-5 !py-2 text-sm">
        {loading ? "Opening..." : "Manage billing"}
      </button>
      {error && (
        <span className="text-sm text-[#ff8a8a]" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
