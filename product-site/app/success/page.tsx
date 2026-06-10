import Link from "next/link";
import { stripe } from "@/lib/stripe";
import { upsertLicenseFromSubscription } from "@/lib/license";
import CopyButton from "@/components/CopyButton";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Post-purchase page. We resolve the license key from the checkout session.
 * The Stripe webhook normally creates the row, but the redirect can beat it,
 * so we provision idempotently here too (same shared upsert) to guarantee the
 * user always sees a working key.
 */
async function resolveLicenseKey(
  sessionId: string | undefined,
): Promise<string | null> {
  if (!sessionId) return null;
  try {
    const session = await stripe.checkout.sessions.retrieve(sessionId, {
      expand: ["subscription"],
    });
    const sub = session.subscription;
    if (!sub || typeof sub === "string") {
      // Fall back to a fetch if it did not expand.
      if (typeof sub === "string") {
        const full = await stripe.subscriptions.retrieve(sub);
        return upsertLicenseFromSubscription(full);
      }
      return null;
    }
    return upsertLicenseFromSubscription(sub);
  } catch (err) {
    console.error("success resolve error:", err);
    return null;
  }
}

export default async function SuccessPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;
  const licenseKey = await resolveLicenseKey(session_id);

  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-20">
      <div className="panel w-full max-w-xl p-8 shadow-glow-lg">
        <div className="label-mono mb-3 text-accent/80">Trial started</div>
        <h1 className="text-3xl font-bold tracking-tight">You are all set.</h1>
        <p className="mt-3 leading-relaxed text-ink-dim">
          Your 7 day free trial is live. Below is your license key. You will
          paste it into the app&apos;s Settings to unlock it.
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
              Download [PRODUCT_NAME]
            </a>
          ) : (
            <button className="btn-primary opacity-50" disabled>
              Download [PRODUCT_NAME]
            </button>
          )}
          <Link href="/" className="btn-ghost">
            Back to home
          </Link>
        </div>

        {/* Open question, on screen only per the brief: emailing the key. */}
        <p className="mt-8 border-t border-panel-border pt-5 text-xs text-ink-faint">
          Open question for the team: should the license key also be emailed on
          purchase? Right now it is shown here only.
        </p>
      </div>
    </main>
  );
}
