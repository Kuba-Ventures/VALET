export const metadata = {
  title: "VALET: Contact",
  description: "Get in touch about questions, press, or partnership ideas.",
};

type Row = { label: string; email: string };

const ROWS: Row[] = [
  { label: "General", email: "hello@valet-voice.com" },
  { label: "Support", email: "support@valet-voice.com" },
  { label: "Press", email: "press@valet-voice.com" },
];

export default function ContactPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell">
        <p className="eyebrow">Contact</p>
        <h1 className="mt-4 h-display text-4xl md:text-6xl text-ink">
          Get in touch.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-dim">
          Questions, press, or partnership ideas. We read everything and reply in
          plain words.
        </p>

        <div className="mt-14 grid max-w-2xl gap-4">
          {ROWS.map((row) => (
            <div
              key={row.label}
              className="flex flex-col gap-2 rounded-lg border border-panel-border bg-panel p-6 sm:flex-row sm:items-center sm:justify-between sm:gap-6"
            >
              <span className="label-mono">{row.label}</span>
              <a
                href={`mailto:${row.email}`}
                className="font-mono text-ink transition-colors hover:text-accent"
              >
                {row.email}
              </a>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
