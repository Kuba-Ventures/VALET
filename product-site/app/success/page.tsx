import Link from "next/link";
import Stripe from "stripe";
import { stripe } from "@/lib/stripe";
import {
  upsertLicenseFromSubscription,
  subscriptionIsComp,
} from "@/lib/license";
import CopyButton from "@/components/CopyButton";
import DataLayerEvent from "@/components/DataLayerEvent";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Plan = "pro" | "ultra" | null;

function planFromPriceId(priceId: string | undefined): Plan {
  if (!priceId) return null;
  if (priceId === process.env.STRIPE_PRICE_ID_PRO) return "pro";
  if (priceId === process.env.STRIPE_PRICE_ID_ULTRA) return "ultra";
  return null;
}

/**
 * Post-purchase resolution from the checkout session. The Stripe webhook
 * normally creates the license row, but the redirect can beat it, so we
 * provision idempotently here too (same shared upsert) to guarantee the user
 * always sees a working key. Also derives the plan for analytics.
 */
async function resolveCheckout(
  sessionId: string | undefined,
): Promise<{ licenseKey: string | null; plan: Plan; comp: boolean }> {
  if (!sessionId) return { licenseKey: null, plan: null, comp: false };
  try {
    const session = await stripe.checkout.sessions.retrieve(sessionId, {
      expand: ["subscription"],
    });
    const raw = session.subscription;
    const subId = typeof raw === "string" ? raw : raw?.id;
    if (!subId) return { licenseKey: null, plan: null, comp: false };
    // Re-retrieve with the discount expanded so we can tell a comp (VIP)
    // grant apart from a normal paid trial.
    const sub = await stripe.subscriptions.retrieve(subId, {
      expand: ["discounts"],
    });
    const licenseKey = await upsertLicenseFromSubscription(sub);
    const plan = planFromPriceId(sub.items?.data?.[0]?.price?.id);
    return { licenseKey, plan, comp: subscriptionIsComp(sub) };
  } catch (err) {
    console.error("success resolve error:", err);
    return { licenseKey: null, plan: null, comp: false };
  }
}

export default async function SuccessPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;
  const { licenseKey, plan, comp } = await resolveCheckout(session_id);

  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-20">
      {licenseKey && (
        <DataLayerEvent
          event="trial_started"
          plan={plan}
          // Comp grants carry no revenue, so report 0 for them.
          value={comp ? 0 : plan === "ultra" ? 50 : 20}
        />
      )}
      <div className="panel w-full max-w-xl p-8 shadow-glow-lg">
        <div className="label-mono mb-3 text-accent/80">
          {comp ? "VIP · Lifetime access" : "Trial started"}
        </div>
        <h1 className="text-3xl font-bold tracking-tight">You are all set.</h1>
        <p className="mt-3 leading-relaxed text-ink-dim">
          {comp
            ? "Your lifetime free Ultra access is live, no card and no charge. Below is your license key. You will paste it into the app's Settings to unlock it."
            : "Your 7 day free trial is live. Below is your license key. You will paste it into the app's Settings to unlock it."}
        </p>

        <div className="mt-8">
          <div className="label-mono mb-2">Your license key</div>
          {licenseKey ? (
            <div className="flex items-center gap-3">
              <code className="flex-1 overflow-x-auto rounded-md border border-panel-border bg-bg px-4 py-3 font-mono text-sm text-accent">
                {licenseKey}
              </code>
              <CopyButton value={licenseKey} />
            </div>
          ) : (
            <div className="rounded-md border border-panel-border bg-bg px-4 py-3 text-sm text-ink-dim">
              Your license is being provisioned. Refresh this page in a few
              seconds, or check your billing email.
            </div>
          )}
        </div>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          {licenseKey ? (
            <a
              href={`/api/download?key=${encodeURIComponent(licenseKey)}`}
              className="btn-primary"
            >
              Download VALET
            </a>
          ) : (
            <button className="btn-primary opacity-50" disabled>
              Download VALET
            </button>
          )}
          <Link href="/account" className="btn-ghost">
            Go to your account
          </Link>
        </div>

        {/* We also email the key (see lib/email.ts), so a closed tab never
            loses it. Point the buyer at their account to manage everything. */}
        <p className="mt-8 border-t border-panel-border pt-5 text-sm text-ink-dim">
          We also emailed this key to you.{" "}
          <Link href="/account/signup" className="text-accent hover:underline">
            Create an account
          </Link>{" "}
          with the same email to manage your license, usage and billing in one
          place.
        </p>
      </div>
    </main>
  );
}
