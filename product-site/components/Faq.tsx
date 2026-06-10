const FAQS = [
  {
    q: "What does it run on?",
    a: "VALET runs on your own Mac. You download and install it, then it works locally and acts on your machine. An agent that controls your computer has to run on your computer, so there is no browser-only version.",
  },
  {
    q: "What does the free trial include?",
    a: "Full access for 7 days. You enter a card up front, but nothing is charged until the trial ends. If you cancel before then, you pay nothing.",
  },
  {
    q: "How do I cancel?",
    a: "Cancel anytime from your billing page. Access continues until the end of the period you already paid for, and you are not charged again.",
  },
  {
    q: "Do I need my own API key?",
    a: "No. The intelligence is included in the subscription. You do not sign up for a model provider or paste in any keys of your own.",
  },
  {
    q: "What about my privacy?",
    a: "It runs on your machine and reads only what you point it at to do the task you asked for. Mail access is read-only by design. We keep the data path as small as the job requires.",
  },
];

export default function Faq() {
  return (
    <section className="mx-auto max-w-shell px-6 py-24">
      <div className="label-mono mb-4">FAQ</div>
      <h2 className="mb-12 max-w-2xl text-3xl font-bold tracking-tight md:text-4xl">
        Straight answers.
      </h2>

      <div className="mx-auto max-w-3xl divide-y divide-panel-border border-y border-panel-border">
        {FAQS.map((f) => (
          <details key={f.q} className="group py-5">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-lg font-medium">
              {f.q}
              <span className="text-accent transition-transform group-open:rotate-45" aria-hidden>
                +
              </span>
            </summary>
            <p className="mt-3 max-w-2xl leading-relaxed text-ink-dim">{f.a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}
