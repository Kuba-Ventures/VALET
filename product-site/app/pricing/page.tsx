import CheckoutButton from "@/components/CheckoutButton";

export const metadata = {
  title: "VALET: Pricing",
  description: "Try the full assistant free for 7 days. Keep it for $20 a month. Teams get tailored PRO bundles.",
  alternates: { canonical: "/pricing" },
};

// PRO "Contact us" lead form — collects contact details + use case so the team
// can build bundle pricing (architects, web agencies, marketers).
const PRO_CONTACT_URL =
  "https://docs.google.com/forms/d/e/1FAIpQLSeYJ7Z8l-pwcWe8tcauP1n7otjazedE7F5_iHE0h0dwKDoQDA/viewform";

type Tier = {
  id: "trial" | "pro" | "contact";
  name: string;
  price: string;
  cadence: string;
  blurb: string;
  features: string[];
  featured?: boolean;
};

const TIERS: Tier[] = [
  {
    id: "trial",
    name: "Free trial",
    price: "Free",
    cadence: "for 7 days",
    blurb: "Try the full assistant. No charge for 7 days, cancel anytime.",
    features: [
      "Hands-free voice control across your apps",
      "Fast answers and deep background work",
      "Read and act on calendar, mail, notes, and browser",
      "Everything in the monthly plan, free for a week",
    ],
  },
  {
    id: "pro",
    name: "Personal",
    price: "$20",
    cadence: "/month",
    blurb: "The full assistant for everyday use. Free for 7 days, then $20/month.",
    featured: true,
    features: [
      "Unlimited voice control across your apps",
      "Fast answers and deep background work",
      "Acts across any app: calendar, mail, notes, browser, and beyond",
      "All intelligence included",
      "Email support",
    ],
  },
  {
    id: "contact",
    name: "PRO",
    price: "Custom",
    cadence: "pricing",
    blurb: "Bundles built for teams: architects, web agencies, and marketers.",
    features: [
      "Everything in the Personal plan",
      "Bundles tailored to how your team works",
      "Seats for your whole team",
      "Onboarding and priority support",
      "Pricing shaped to your use case",
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
  if (tier.id === "contact") {
    return (
      <a
        href={PRO_CONTACT_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="btn-ghost w-full justify-center"
      >
        Contact us
      </a>
    );
  }
  // The free-trial and Personal cards both start the same $20 plan (7-day trial).
  return (
    <CheckoutButton
      plan="pro"
      label={tier.id === "trial" ? "Try free" : "Start 7-day trial"}
      variant={tier.featured ? "primary" : "ghost"}
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
          Try the full assistant free for 7 days. Keep it for $20 a month, with
          nothing to bring and nothing to configure. Running a team? PRO bundles
          are tailored to how you work.
        </p>

        <div className="mt-14 grid items-stretch gap-5 md:grid-cols-3">
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

                <div className="mt-auto pt-8">
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
