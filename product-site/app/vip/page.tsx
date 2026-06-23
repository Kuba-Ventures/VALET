import Link from "next/link";
import CheckoutButton from "@/components/CheckoutButton";
import { createSupabaseServerClient } from "@/lib/auth/server";

// VIP grant link: possession of the URL (and/or the promotion code) is the
// gate, so keep it out of search indexes.
export const metadata = {
  title: "VALET: VIP access",
  robots: { index: false, follow: false },
};

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export default async function VipPage() {
  // VIP must be claimed by a signed-in account: the activation stamps the
  // account UUID onto the Stripe subscription so the license binds to it
  // immediately. Without this gate the comp license floated unlinked (NULL
  // user_id) because nothing tied the $0 checkout back to an account.
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <main className="pt-32 pb-24">
      <div className="shell max-w-xl">
        <p className="eyebrow">VIP</p>
        <h1 className="mt-4 h-display text-4xl md:text-5xl text-ink">
          Ultra, on the house.
        </h1>
        <p className="mt-6 text-lg leading-relaxed text-ink-dim">
          You&apos;ve been given complimentary Ultra access. Every capability,
          no charge and no card.
        </p>

        {user ? (
          <>
            <p className="mt-6 text-lg leading-relaxed text-ink-dim">
              Activate it below, then download VALET and sign in to the app with
              this account.
            </p>
            <p className="mt-4 font-mono text-xs text-ink-faint">
              Signed in as {user.email}
            </p>
            <div className="mt-8">
              <CheckoutButton comp label="Activate VIP Ultra" />
            </div>
            <p className="mt-5 font-mono text-xs text-ink-faint">
              No card required · cancel anytime
            </p>
          </>
        ) : (
          <>
            <p className="mt-6 text-lg leading-relaxed text-ink-dim">
              First, create your account (or sign in). We&apos;ll link your
              lifetime Ultra license to it automatically, so it&apos;s ready the
              moment you open the app.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link
                href="/account/signup?next=/vip"
                className="btn-primary"
              >
                Create your account
              </Link>
              <Link href="/account/login?next=/vip" className="btn-ghost">
                Sign in
              </Link>
            </div>
            <p className="mt-5 font-mono text-xs text-ink-faint">
              No card required · cancel anytime
            </p>
          </>
        )}
      </div>
    </main>
  );
}
