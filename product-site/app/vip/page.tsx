import CheckoutButton from "@/components/CheckoutButton";

// VIP grant link — possession of the URL (and/or the promotion code) is the
// gate, so keep it out of search indexes.
export const metadata = {
  title: "VALET: VIP access",
  robots: { index: false, follow: false },
};

export default function VipPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell max-w-xl">
        <p className="eyebrow">VIP</p>
        <h1 className="mt-4 h-display text-4xl md:text-5xl text-ink">
          Ultra, on the house.
        </h1>
        <p className="mt-6 text-lg leading-relaxed text-ink-dim">
          You&apos;ve been given complimentary Ultra access. Every capability,
          no charge and no card. Activate it below, then download VALET and sign
          in to the app with this account.
        </p>

        <div className="mt-8">
          <CheckoutButton comp label="Activate VIP Ultra" />
        </div>

        <p className="mt-5 font-mono text-xs text-ink-faint">
          No card required · cancel anytime
        </p>
      </div>
    </main>
  );
}
