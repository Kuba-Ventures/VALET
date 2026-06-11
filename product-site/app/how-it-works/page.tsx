export const metadata = {
  title: "VALET: How it works",
  description: "Three steps to a working assistant: install and sign in, speak, and it gets done.",
};

type Step = {
  index: string;
  title: string;
  body: string;
};

const STEPS: Step[] = [
  {
    index: "01",
    title: "Install and sign in",
    body: "Download VALET, drop in your license key, and grant the permissions it needs to act on your Mac.",
  },
  {
    index: "02",
    title: "Speak",
    body: "Press to talk, or just start. Ask for anything, from a quick fact to a multi-step task across your apps.",
  },
  {
    index: "03",
    title: "It gets done",
    body: "Simple things answer at once. Bigger jobs run in the background and report back when they’re finished.",
  },
];

export default function HowItWorksPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell">
        <p className="eyebrow">How it works</p>
        <h1 className="mt-4 h-display text-4xl md:text-6xl text-ink">
          Three steps to a working assistant.
        </h1>

        <div className="mt-16 border-t border-panel-border">
          {STEPS.map((step) => (
            <div
              key={step.index}
              className="grid grid-cols-1 gap-4 border-b border-panel-border py-12 md:grid-cols-[auto_1fr] md:gap-12 md:py-16"
            >
              <div className="font-mono text-3xl text-accent-deep md:text-4xl">
                {step.index}
              </div>
              <div className="max-w-2xl">
                <h2 className="h-display text-2xl font-bold text-ink md:text-3xl">
                  {step.title}
                </h2>
                <p className="mt-4 text-lg leading-relaxed text-ink-dim">
                  {step.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
