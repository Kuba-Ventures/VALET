"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

/**
 * Resumes a purchase after the buyer has an account. This is the `next`
 * destination the signup flow forwards to once the confirmation link signs them
 * in: it auto-creates the Stripe Checkout session (now that /api/checkout can
 * bind the license to their verified account) and sends them to Stripe.
 *
 * A signed-in visitor who reaches it directly just checks out. If the session is
 * missing or expired, /api/checkout returns 401 and we bounce to login, coming
 * right back here afterward so the purchase still completes.
 */
function CheckoutStartInner() {
  const params = useSearchParams();
  const plan = params.get("plan") === "ultra" ? "ultra" : "pro";
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  async function start() {
    setError(null);
    try {
      const res = await fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan }),
      });

      if (res.status === 401) {
        // No session yet (e.g. link opened in a different browser). Send them to
        // login and return here to finish the purchase.
        const next = `/checkout/start?plan=${plan}`;
        window.location.href = `/account/login?next=${encodeURIComponent(next)}`;
        return;
      }

      const data = await res.json();
      if (!res.ok || !data.url) {
        throw new Error(data.error || "Could not start checkout.");
      }
      window.location.href = data.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    }
  }

  useEffect(() => {
    // Guard against React 18 StrictMode's double-invoke in dev so we never fire
    // two checkout sessions.
    if (started.current) return;
    started.current = true;
    void start();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="panel w-full max-w-md p-8 text-center shadow-glow-lg">
      {error ? (
        <>
          <div className="label-mono mb-3 text-accent/80">Checkout</div>
          <h1 className="text-2xl font-bold tracking-tight">
            We couldn&apos;t open checkout
          </h1>
          <p className="mt-3 leading-relaxed text-ink-dim">{error}</p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
            <button onClick={() => void start()} className="btn-primary">
              Try again
            </button>
            <Link href="/pricing" className="btn-ghost">
              Back to pricing
            </Link>
          </div>
        </>
      ) : (
        <>
          <div className="label-mono mb-3 text-accent/80">
            {plan === "ultra" ? "Ultra · 7-day trial" : "7-day trial"}
          </div>
          <h1 className="text-2xl font-bold tracking-tight">
            Opening secure checkout…
          </h1>
          <p className="mt-3 leading-relaxed text-ink-dim">
            One moment while we hand you off to Stripe. You won&apos;t be charged
            during the trial.
          </p>
        </>
      )}
    </div>
  );
}

export default function CheckoutStartPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-20">
      <Suspense
        fallback={
          <div className="panel w-full max-w-md p-8 text-center shadow-glow-lg">
            <h1 className="text-2xl font-bold tracking-tight">
              Opening secure checkout…
            </h1>
          </div>
        }
      >
        <CheckoutStartInner />
      </Suspense>
    </main>
  );
}
