const STEPS = [
  {
    n: "1",
    title: "Install and sign in",
    body: "Download VALET, drop in your license key, and grant the permissions it needs to act on your machine.",
  },
  {
    n: "2",
    title: "Speak",
    body: "Press to talk or just start. Ask for anything, from a quick fact to a multi-step task across your apps.",
  },
  {
    n: "3",
    title: "It gets it done",
    body: "Simple things answer instantly. Bigger jobs run in the background and report back when finished.",
  },
];

export default function HowItWorks() {
  return (
    <section id="how" className="mx-auto max-w-shell px-6 py-24">
      <div className="label-mono mb-4">How it works</div>
      <h2 className="mb-12 max-w-2xl text-3xl font-bold tracking-tight md:text-4xl">
        Three steps to a working assistant.
      </h2>

      <div className="grid gap-px overflow-hidden rounded-lg border border-panel-border bg-panel-border md:grid-cols-3">
        {STEPS.map((s) => (
          <div key={s.n} className="bg-bg-elevated p-8">
            <div className="mb-5 flex h-10 w-10 items-center justify-center rounded-full border border-accent/40 font-mono text-accent">
              {s.n}
            </div>
            <h3 className="mb-3 text-lg font-semibold">{s.title}</h3>
            <p className="leading-relaxed text-ink-dim">{s.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
