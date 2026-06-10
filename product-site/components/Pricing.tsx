import CheckoutButton from "./CheckoutButton";

const INCLUDED = [
  "Voice control across your apps",
  "Fast answers and deep background work",
  "All intelligence included, no API key needed",
  "Free updates while subscribed",
  "Cancel anytime, no questions",
];

export default function Pricing() {
  return (
    <section id="pricing" className="mx-auto max-w-shell px-6 py-24">
      <div className="label-mono mb-4">Pricing</div>
      <h2 className="mb-12 max-w-2xl text-3xl font-bold tracking-tight md:text-4xl">
        One plan. Everything included.
      </h2>

      <div className="mx-auto max-w-md">
        <div className="panel relative overflow-hidden p-8 shadow-glow-lg">
          <div className="pointer-events-none absolute -top-24 left-1/2 h-48 w-48 -translate-x-1/2 rounded-full bg-accent/20 blur-3xl" />

          <div className="relative">
            <div className="label-mono mb-2">[PRODUCT_NAME]</div>
            <div className="flex items-end gap-2">
              <span className="text-5xl font-bold tracking-tight">$20</span>
              <span className="mb-1.5 text-ink-dim">per month</span>
            </div>
            <p className="mt-3 text-ink-dim">
              7 day free trial. Card required up front, no charge until the trial
              ends.
            </p>

            <ul className="mt-7 space-y-3">
              {INCLUDED.map((item) => (
                <li key={item} className="flex items-start gap-3">
                  <span className="mt-1 text-accent" aria-hidden>
                    &#10003;
                  </span>
                  <span className="text-ink">{item}</span>
                </li>
              ))}
            </ul>

            <div className="mt-8">
              <CheckoutButton className="w-full [&>button]:w-full" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
