import Reveal from "../Reveal";

const FEATURES = [
  {
    title: "Talk from anywhere.",
    body: "Hold ⌃⌥ and speak from any app. The orb listens, no window-switching.",
  },
  {
    title: "Instant, no typing.",
    body: "Open apps, find files, jump to settings, run system actions. Sub-second, no model round-trip.",
  },
  {
    title: "It actually does it.",
    body: "VALET glides your cursor and clicks for you. Native Mac control with a confirm card, kill switch, and Escape to stop.",
  },
  {
    title: "Ship it by voice.",
    body: "Dictate to Claude Code and Cursor and spin up whole projects without touching the keyboard.",
  },
  {
    title: "Or it shows you how.",
    body: "Guided walkthroughs glide your cursor to each step and wait. It teaches, it doesn't take over.",
  },
];

export default function HomeFeatures() {
  return (
    <section className="lp-features">
      <div className="shell py-24">
        <Reveal>
          <p className="eyebrow">What it does</p>
          <h2 className="h-display mt-5 max-w-3xl text-[clamp(2rem,5vw,3.25rem)] text-ink">
            Speed is the product.
          </h2>
        </Reveal>

        <div className="lp-feature-grid">
          {FEATURES.map((f, i) => (
            <Reveal key={f.title} delay={i * 70}>
              <div className="lp-feature-card">
                <span className="lp-feature-num">{String(i + 1).padStart(2, "0")}</span>
                <h3 className="h-display lp-feature-title text-ink">{f.title}</h3>
                <p className="lp-feature-body">{f.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
