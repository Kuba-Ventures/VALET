import CheckoutButton from "@/components/CheckoutButton";

export const metadata = {
  title: "VALET: Pricing",
  description: "Start free. Move up when you want more reach. Every paid plan includes the intelligence.",
};

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
    blurb: "Try talking to your Mac. No card required.",
    features: [
      "Hands-free voice, plain words",
      "Everyday questions and answers",
      "Read your calendar, mail, and notes",
      "Up to 20 actions a day",
    ],
  },
  {
    id: "pro",
    name: "Pro",
    price: "$20",
    cadence: "/month",
    blurb: "The full assistant for everyday use. 7-day trial, no charge until it ends.",
    featured: true,
    features: [
      "Unlimited voice control across your apps",
      "Fast answers and deep background work",
      "Acts across calendar, mail, notes, browser, files",
      "All intelligence included",
      "Email support",
    ],
  },
  {
    id: "ultra",
    name: "Ultra",
    price: "$50",
    cadence: "/month",
    blurb: "For heavy, long-running work that runs itself. 7-day trial.",
    features: [
      "Long-running autonomous jobs",
      "Deep research with the most capable models",
      "Higher limits and priority responses",
      "Several tasks at once",
      "Priority support",
    ],
  },
];

function Check() {
  return (
    <span
      className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center text-accent"
      aria-hidden
    >
      &#10003;
    </span>
  );
}

function Cta({ tier }: { tier: Tier }) {
  if (tier.id === "free") {
    return (
      <a href="/pricing" className="btn-ghost w-full justify-center">
        Get started free
      </a>
    );
  }
  return (
    <CheckoutButton
      plan={tier.id}
      label="Start 7-day trial"
      variant="primary"
      className="w-full [&>button]:w-full"
    />
  );
}

export default function PricingPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell">
        <p className="eyebrow">Pricing</p>
        <h1 className="mt-4 h-display text-4xl md:text-6xl text-ink">
          Pick your level.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-dim">
          Start free. Move up when you want more reach. Every paid plan includes
          the intelligence, nothing to bring and nothing to configure.
        </p>

        <div className="mt-14 grid items-start gap-5 md:grid-cols-3">
          {TIERS.map((tier) => (
            <div
              key={tier.id}
              className={`panel relative flex h-full flex-col overflow-hidden p-7 ${
                tier.featured ? "border-accent/40 shadow-glow-lg" : "shadow-panel"
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
                  <span className="text-4xl font-bold tracking-tight text-ink">
                    {tier.price}
                  </span>
                  <span className="mb-1 text-ink-dim">{tier.cadence}</span>
                </div>
                <p className="mt-3 min-h-[3.5rem] text-sm leading-relaxed text-ink-dim">
                  {tier.blurb}
                </p>

                <ul className="mt-6 space-y-3">
                  {tier.features.map((item) => (
                    <li key={item} className="flex items-start gap-3">
                      <Check />
                      <span className="text-sm text-ink">{item}</span>
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

        <p className="mt-10 text-center font-mono text-xs text-ink-faint">
          All plans cancel anytime · prices in USD
        </p>
      </div>
    </main>
  );
}
