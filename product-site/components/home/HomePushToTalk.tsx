import Reveal from "../Reveal";

/* A stylized Mac keyboard with Control + Option lit in the VALET palette — the
   one shortcut that summons VALET from any app. Decorative (aria-hidden); the
   shortcut is also stated in the copy for screen readers. */
function Keyboard() {
  const rowA = Array.from({ length: 13 });
  const rowB = Array.from({ length: 12 });
  return (
    <div className="lp-kbd" aria-hidden="true">
      <div className="lp-kbd-glow" />
      <div className="lp-kbd-deck">
        <div className="lp-kbd-row">
          {rowA.map((_, i) => (
            <span key={i} className="lp-kbd-key" />
          ))}
        </div>
        <div className="lp-kbd-row">
          {rowB.map((_, i) => (
            <span key={i} className="lp-kbd-key" />
          ))}
        </div>
        <div className="lp-kbd-row lp-kbd-row--mods">
          <span className="lp-kbd-key lp-kbd-key--fn">fn</span>
          <span className="lp-kbd-key lp-kbd-key--lit lp-kbd-key--mod">
            <b>⌃</b>
            <small>control</small>
          </span>
          <span className="lp-kbd-key lp-kbd-key--lit lp-kbd-key--mod">
            <b>⌥</b>
            <small>option</small>
          </span>
          <span className="lp-kbd-key lp-kbd-key--mod">
            <b>⌘</b>
            <small>command</small>
          </span>
          <span className="lp-kbd-key lp-kbd-key--space" />
          <span className="lp-kbd-key lp-kbd-key--mod">
            <b>⌘</b>
          </span>
          <span className="lp-kbd-key lp-kbd-key--mod">
            <b>⌥</b>
          </span>
        </div>
      </div>
    </div>
  );
}

export default function HomePushToTalk() {
  return (
    <section className="lp-ptt">
      <div className="shell lp-ptt-inner">
        <Reveal>
          <p className="eyebrow">Push to talk</p>
          <h2 className="h-display lp-ptt-title text-ink">
            One shortcut. From any app.
          </h2>
          <p className="lp-ptt-sub">
            Hold <span className="lp-ptt-keys">Control + Option</span> and start
            talking. VALET listens from wherever you are. No window to find, no
            button to click.
          </p>
        </Reveal>
        <Reveal delay={90}>
          <Keyboard />
        </Reveal>
      </div>
    </section>
  );
}
