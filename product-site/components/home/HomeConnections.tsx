import Reveal from "../Reveal";

/* Official app/service marks. Apple + locally-installed app icons are the real
   macOS icons; the web services use each brand's official logo. */
const LIVE = [
  { name: "Apple Calendar", logo: "/logos/apple-calendar.png" },
  { name: "Google Calendar", logo: "/logos/google-calendar.svg" },
  { name: "Apple Mail", logo: "/logos/apple-mail.png" },
  { name: "Gmail", logo: "/logos/gmail.png" },
  { name: "Apple Notes", logo: "/logos/apple-notes.png" },
  { name: "Apple Contacts", logo: "/logos/apple-contacts.png" },
  { name: "Chrome", logo: "/logos/chrome.png" },
  { name: "Claude Code", logo: "/logos/claude.png" },
  { name: "Cursor", logo: "/logos/cursor.png" },
  { name: "Finder / Spotlight", logo: "/logos/finder.png" },
  { name: "System Settings", logo: "/logos/system-settings.png" },
];

const SOON: { name: string; logo: string | null }[] = [
  { name: "Linear", logo: "/logos/linear.svg" },
  { name: "Slack", logo: "/logos/slack.png" },
  { name: "Notion", logo: "/logos/notion.svg" },
  { name: "and more", logo: null },
];

function Logo({ src, name }: { src: string | null; name: string }) {
  if (!src) {
    return (
      <svg viewBox="0 0 24 24" fill="none" className="lp-conn-logo lp-conn-logo--node" aria-hidden="true">
        <circle cx="12" cy="12" r="5.5" stroke="currentColor" strokeWidth="1.4" />
        <circle cx="12" cy="12" r="1.8" fill="currentColor" />
      </svg>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={`${name} logo`} className="lp-conn-logo" loading="lazy" />;
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
          <p className="lp-conn-lead">
            VALET opens and controls any app on your Mac, whether it shipped with
            macOS or you downloaded it from the web. It sees your screen and acts
            on it, so it works with every app, not just a chosen few.
          </p>
        </Reveal>

        <Reveal>
          <p className="label-mono lp-conn-gridlabel">The ones it knows by name</p>
          <div className="lp-conn-grid">
            {LIVE.map((c) => (
              <div key={c.name} className="lp-conn">
                <Logo src={c.logo} name={c.name} />
                <span className="lp-conn-name">{c.name}</span>
              </div>
            ))}
            {SOON.map((c) => (
              <div key={c.name} className="lp-conn lp-conn-soon">
                <Logo src={c.logo} name={c.name} />
                <span className="lp-conn-name">{c.name}</span>
                <span className="lp-conn-tag">soon</span>
              </div>
            ))}
          </div>
        </Reveal>

        <p className="mt-6 font-mono text-xs text-ink-faint">
          Coming via MCP, the open protocol for connecting assistants to tools.
        </p>
      </div>
    </section>
  );
}
