"use client";

import { useState } from "react";

/**
 * Starts the subscription checkout: creates a session server-side via
 * /api/checkout, then sends the browser to the returned Stripe URL.
 */
export default function CheckoutButton({
  label = "Start 7 day free trial",
  variant = "primary",
  className = "",
}: {
  label?: string;
  variant?: "primary" | "ghost";
  className?: string;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startCheckout() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/checkout", { method: "POST" });
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
