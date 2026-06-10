import CheckoutButton from "./CheckoutButton";

type Tier = {
  id: "free" | "pro" | "ultra";
  name: string;
  price: string;
  cadence: string;
  blurb: string;
  features: string[];
  featured?: boolean;
};

const TIERS: Tier[] = [
  {
    id: "free",
    name: "Free",
    price: "$0",
    cadence: "forever",
    blurb: "Try talking to your computer. No card required.",
    features: [
      "Hands-free voice, ask in plain words",
      "Quick questions and everyday answers",
      "Read your calendar, mail, and notes",
      "Up to 20 actions per day",
      "Community support",
    ],
  },
  {
    id: "pro",
    name: "Pro",
    price: "$20",
    cadence: "per month",
    blurb: "The full assistant for daily driving. 7 day free trial, card up front, no charge until it ends.",
    featured: true,
    features: [
      "Everything in Free, plus:",
      "Unlimited voice control across all your apps",
      "Fast answers and deep background work",
      "Acts across calendar, mail, notes, browser, and files",
      "All intelligence included, no API key needed",
      "Email support",
    ],
  },
  {
    id: "ultra",
    name: "Ultra",
    price: "$50",
    cadence: "per month",
    blurb: "For power users running heavy, autonomous work. 7 day free trial.",
    features: [
      "Everything in Pro, plus:",
      "Long-running autonomous agents (dispatch coding and research jobs)",
      "Deep research with the most capable models",
      "Higher limits and priority responses",
      "Run several tasks at once",
      "Priority support",
    ],
  },
];

function Cta({ tier }: { tier: Tier }) {
  if (tier.id === "free") {
    // Free tier has no Stripe subscription; this CTA is a placeholder until the
    // free flow is decided (see open question in the PR).
    return (
      <a href="#how" className="btn-ghost w-full justify-center">
        Get started free
      </a>
    );
  }
  return (
    <CheckoutButton
      plan={tier.id}
      label="Start 7 day free trial"
      variant={tier.featured ? "primary" : "primary"}
      className="w-full [&>button]:w-full"
    />
  );
}

export default function Pricing() {
  return (
    <section id="pricing" className="mx-auto max-w-shell px-6 py-24">
      <div className="label-mono mb-4">Pricing</div>
      <h2 className="mb-3 max-w-2xl text-3xl font-bold tracking-tight md:text-4xl">
        Pick your level.
      </h2>
      <p className="mb-12 max-w-2xl text-ink-dim">
        Start free, upgrade when you want more reach and more horsepower. Every
        paid plan includes the intelligence, no API key of your own required.
      </p>

      <div className="grid items-start gap-5 md:grid-cols-3">
        {TIERS.map((tier) => (
          <div
            key={tier.id}
            className={`panel relative flex h-full flex-col overflow-hidden p-7 ${
              tier.featured
                ? "border-accent/40 shadow-glow-lg"
                : "shadow-panel"
            }`}
          >
            {tier.featured && (
              <>
                <div className="pointer-events-none absolute -top-24 left-1/2 h-48 w-48 -translate-x-1/2 rounded-full bg-accent/20 blur-3xl" />
                <div className="absolute right-5 top-5 rounded-full border border-accent/40 px-3 py-1 font-mono text-[0.65rem] uppercase tracking-[0.16em] text-accent">
                  Most popular
                </div>
              </>
            )}

            <div className="relative flex h-full flex-col">
              <div className="label-mono mb-2">{tier.name}</div>
              <div className="flex items-end gap-2">
                <span className="text-4xl font-bold tracking-tight">
                  {tier.price}
                </span>
                <span className="mb-1 text-ink-dim">{tier.cadence}</span>
              </div>
              <p className="mt-3 min-h-[3.5rem] text-sm leading-relaxed text-ink-dim">
                {tier.blurb}
              </p>

              <ul className="mt-6 space-y-3">
                {tier.features.map((item, i) => (
                  <li key={item} className="flex items-start gap-3">
                    {i === 0 && tier.id !== "free" ? (
                      <span className="text-sm font-medium text-ink-dim">
                        {item}
                      </span>
                    ) : (
                      <>
                        <span className="mt-0.5 text-accent" aria-hidden>
                          &#10003;
                        </span>
                        <span className="text-sm text-ink">{item}</span>
                      </>
                    )}
                  </li>
                ))}
              </ul>

              <div className="mt-8 pt-2">
                <Cta tier={tier} />
              </div>
            </div>
          </div>
        ))}
      </div>

      <p className="mt-8 text-center text-sm text-ink-faint">
        All plans cancel anytime. Prices in USD.
      </p>
    </section>
  );
}
