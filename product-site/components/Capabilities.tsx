const CAPS = [
  {
    tag: "01",
    title: "Talk to your computer",
    body: "Say what you want in plain language. No commands to memorize, no menus to hunt through. It listens and responds in the moment.",
  },
  {
    tag: "02",
    title: "It acts across your apps",
    body: "Calendar, mail, notes, the browser, your files. It reaches into the tools you already use and does the thing, rather than telling you how.",
  },
  {
    tag: "03",
    title: "Fast when simple, deep when hard",
    body: "Quick questions come back instantly. Harder work spins up a focused effort in the background and reports when it lands.",
  },
  {
    tag: "04",
    title: "Everything included",
    body: "One subscription covers the intelligence behind it. You do not bring your own API key or wire up any models. It just works.",
  },
];

export default function Capabilities() {
  return (
    <section className="mx-auto max-w-shell px-6 py-24">
      <div className="label-mono mb-4">What it does</div>
      <h2 className="mb-12 max-w-2xl text-3xl font-bold tracking-tight md:text-4xl">
        A command surface for your whole machine.
      </h2>

      <div className="grid gap-5 sm:grid-cols-2">
        {CAPS.map((c) => (
          <div key={c.tag} className="panel p-7 shadow-panel">
            <div className="label-mono mb-4 text-accent/70">{c.tag}</div>
            <h3 className="mb-3 text-xl font-semibold">{c.title}</h3>
            <p className="leading-relaxed text-ink-dim">{c.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
