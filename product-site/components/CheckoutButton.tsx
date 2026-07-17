"use client";

import { useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/auth/client";

/**
 * Starts subscription checkout: creates a session server-side via /api/checkout
 * (passing the plan, or the comp flag for VIP), then sends the browser to the
 * returned Stripe URL. With `comp`, the plan is forced to Ultra server-side and
 * the session is the no-card complimentary flow.
 *
 * Checkout now requires an account (the license binds to it up front, and the
 * buyer can later just log in from the app to auto-activate). So a visitor who
 * isn't signed in is routed to signup first, with `next` pointing back at the
 * step that resumes their purchase after they confirm their email:
 *   - paid → /checkout/start?plan=<plan> (auto-restarts checkout → Stripe)
 *   - comp → /vip (they re-click the VIP button, now signed in)
 */
export default function CheckoutButton({
  plan = "pro",
  comp = false,
  label = "Start 7 day free trial",
  variant = "primary",
  className = "",
}: {
  plan?: "pro" | "ultra";
  comp?: boolean;
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
      event: comp ? "begin_comp" : "begin_checkout",
      plan: comp ? "ultra" : plan,
      value: comp ? 0 : plan === "ultra" ? 50 : 20,
      currency: "USD",
    });

    // Require an account before checkout. If the visitor isn't signed in, send
    // them to signup and come back to resume the purchase. Any failure reading
    // the session is treated as "not signed in" — the checkout route re-checks
    // server-side, so the worst case is a harmless extra signup hop.
    try {
      const supabase = createSupabaseBrowserClient();
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) {
        const next = comp ? "/vip" : `/checkout/start?plan=${plan}`;
        window.location.href = `/account/signup?next=${encodeURIComponent(next)}`;
        return;
      }
    } catch {
      const next = comp ? "/vip" : `/checkout/start?plan=${plan}`;
      window.location.href = `/account/signup?next=${encodeURIComponent(next)}`;
      return;
    }

    try {
      const res = await fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(comp ? { comp: true } : { plan }),
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
