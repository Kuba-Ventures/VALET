import Reveal from "../Reveal";

const LIVE = [
  "Apple Calendar",
  "Google Calendar",
  "Apple Mail",
  "Gmail",
  "Apple Notes",
  "Apple Contacts",
  "Chrome",
  "Claude Code",
  "Cursor",
  "Finder / Spotlight",
  "System Settings",
];

const SOON = ["Linear", "Slack", "Notion", "and more"];

/* One restrained mark per row — a constellation node, not a fake brand logo. */
function Mark() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="lp-conn-mark" aria-hidden="true">
      <circle cx="12" cy="12" r="5.5" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="12" cy="12" r="1.8" fill="currentColor" />
    </svg>
  );
}

export default function HomeConnections() {
  return (
    <section className="lp-connections">
      <div className="shell py-24">
        <Reveal>
          <p className="eyebrow">Connections</p>
          <h2 className="h-display mt-5 max-w-3xl text-[clamp(2rem,5vw,3.25rem)] text-ink">
            You don&apos;t install extensions. You just ask.
          </h2>
        </Reveal>

        <Reveal>
          <div className="lp-conn-grid">
            {LIVE.map((name) => (
              <div key={name} className="lp-conn">
                <Mark />
                <span className="lp-conn-name">{name}</span>
              </div>
            ))}
            {SOON.map((name) => (
              <div key={name} className="lp-conn lp-conn-soon">
                <Mark />
                <span className="lp-conn-name">{name}</span>
                <span className="lp-conn-tag">soon</span>
              </div>
            ))}
          </div>
        </Reveal>

        <p className="mt-6 font-mono text-xs text-ink-faint">
          Coming via MCP — the open protocol for connecting assistants to tools.
        </p>
      </div>
    </section>
  );
}
