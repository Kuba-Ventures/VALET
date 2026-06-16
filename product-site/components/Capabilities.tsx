import Reveal from "./Reveal";

const ICON = "h-6 w-6 stroke-accent";

const CARDS = [
  {
    title: "Plain language",
    body: "Say it how you'd say it to a person. No syntax to learn, no settings to find. It listens and responds in the moment.",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON} strokeWidth="1.6" strokeLinecap="round">
        <rect x="9" y="2.5" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3M8.5 21h7" />
      </svg>
    ),
  },
  {
    title: "Real actions, any app",
    body: "It opens the app, finds the file, writes the reply, books the time — across any app on your Mac, not just a built-in few. Outcomes, not instructions for you to follow.",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 12h13M12 5l7 7-7 7" />
      </svg>
    ),
  },
  {
    title: "Fast, then deep",
    body: "Quick questions answer instantly. Harder jobs spin up a focused effort in the background and report when they land.",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />
      </svg>
    ),
  },
  {
    title: "Nothing to set up",
    body: "One subscription covers all the intelligence inside. No keys, no config, no models to wire up. It just works.",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2.5 4.5 5.5v5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5v-5L12 2.5Z" />
        <path d="M8.8 12l2.2 2.2 4.2-4.4" />
      </svg>
    ),
  },
];

export default function Capabilities() {
  return (
    <section className="border-t border-panel-border">
      <div className="shell py-24">
        <Reveal>
          <p className="eyebrow">What it does</p>
          <h2 className="h-display mt-5 max-w-3xl text-[clamp(2rem,5vw,3.5rem)] text-ink">
            A singular command surface for your entire machine.
          </h2>
        </Reveal>

        <div className="mt-14 grid gap-5 md:grid-cols-2">
          {CARDS.map((c, i) => (
            <Reveal key={c.title} delay={i * 80}>
              <div className="h-full rounded-lg border border-panel-border bg-panel p-7 transition-colors hover:border-accent-soft">
                <div className="flex h-11 w-11 items-center justify-center rounded-md border border-panel-border bg-bg-elevated">
                  {c.icon}
                </div>
                <h3 className="h-display mt-5 text-xl text-ink">{c.title}</h3>
                <p className="mt-3 leading-relaxed text-ink-dim">{c.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
