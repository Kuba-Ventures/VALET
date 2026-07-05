export const metadata = {
  title: "VALET: Terms",
  description: "The terms for using VALET, the voice assistant for macOS.",
  alternates: { canonical: "/terms" },
};

export default function TermsPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell max-w-3xl">
        <p className="eyebrow">Terms</p>
        <h1 className="mt-4 h-display text-4xl md:text-6xl text-ink">Terms.</h1>
        <p className="mt-4 text-ink-faint">Last updated: 2026-06-11</p>

        <p className="mt-8 text-ink-dim leading-relaxed">
          These terms govern your use of VALET. Please read them. They are written to be
          plain and short.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Acceptance</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          By installing or using VALET, you agree to these terms. If you do not agree, do
          not use VALET.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">The service</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          VALET is a voice assistant for macOS. It runs on your Mac and acts on your Mac
          using the permissions you grant it. What it can do depends on the access you
          choose to give it.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Subscriptions and billing</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          VALET is a monthly subscription at $20 per month, billed in USD through Stripe.
          New subscriptions start with a 7-day trial. You can cancel anytime, and your
          access continues through the end of the current billing period. We do not provide
          refunds for partial periods.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Acceptable use</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          Do not use VALET for unlawful or abusive purposes. Fair-use limits apply to keep
          the service reliable for everyone, and we may meter or throttle usage that goes
          well beyond normal individual use.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Your content and permissions</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          You are responsible for what you ask VALET to do. VALET acts with the access you
          grant it on your Mac, so review the permissions you enable and the actions you
          request.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">No warranty</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          VALET is provided as is, on a best-effort basis, without warranties of any kind.
          We do not guarantee that it will be uninterrupted or error-free.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Limitation of liability</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          To the maximum extent permitted by law, our total liability for any claim related
          to VALET is limited to the amount you paid for the service in the 12 months before
          the claim. We are not liable for indirect, incidental, or consequential damages.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Changes to the service and terms</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          We may update VALET and these terms over time. If we make material changes, we
          will update the date above. Continuing to use VALET after changes take effect
          means you accept the updated terms.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Governing law</h2>
        {/* PLACEHOLDER: confirm governing-law jurisdiction before publishing. */}
        <p className="mt-4 text-ink-dim leading-relaxed">
          These terms are governed by the laws of [JURISDICTION], without regard to its
          conflict-of-laws rules.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Contact</h2>
        {/* PLACEHOLDER: confirm contact email before publishing. */}
        <p className="mt-4 text-ink-dim leading-relaxed">
          Questions: reach out via the address on the marketing site.
        </p>
      </div>
    </main>
  );
}
