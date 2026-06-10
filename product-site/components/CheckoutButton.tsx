"use client";

import { useState } from "react";

/**
 * Starts subscription checkout for a given paid plan: creates a session
 * server-side via /api/checkout (passing the plan), then sends the browser to
 * the returned Stripe URL.
 */
export default function CheckoutButton({
  plan = "pro",
  label = "Start 7 day free trial",
  variant = "primary",
  className = "",
}: {
  plan?: "pro" | "ultra";
  label?: string;
  variant?: "primary" | "ghost";
  className?: string;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startCheckout() {
    setLoading(true);
    setError(null);
    // GTM analytics: intent to start a trial. Configure as a tag in GTM.
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({
      event: "begin_checkout",
      plan,
      value: plan === "ultra" ? 50 : 20,
      currency: "USD",
    });
    try {
      const res = await fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan }),
      });
      const data = await res.json();
      if (!res.ok || !data.url) {
        throw new Error(data.error || "Could not start checkout.");
      }
      window.location.href = data.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setLoading(false);
    }
  }

  return (
    <div className={`inline-flex flex-col items-start gap-2 ${className}`}>
      <button
        onClick={startCheckout}
        disabled={loading}
        className={variant === "primary" ? "btn-primary" : "btn-ghost"}
      >
        {loading ? "Opening checkout..." : label}
      </button>
      {error && (
        <span className="text-sm text-[#ff8a8a]" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
