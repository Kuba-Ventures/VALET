import Reveal from "./Reveal";
import Waveform from "./Waveform";
import ActionStack from "./ActionStack";
import Constellation from "./Constellation";

const PANELS = [
  {
    label: "01 / The ask",
    statement: <>You speak.</>,
    sub: "No commands to memorize. No menus to hunt through. You say it the way you'd say it to a person.",
    visual: <Waveform />,
  },
  {
    label: "02 / The work",
    statement: (
      <>
        It <span className="text-ink-dim">acts.</span>
      </>
    ),
    sub: "Not answers about how to do the thing. The thing itself: opened, found, written, scheduled, sent.",
    visual: <ActionStack />,
  },
  {
    label: "03 / The reach",
    statement: <>Everywhere you work.</>,
    sub: "Calendar. Mail. Notes. The browser. Your files. One voice across all of it.",
    visual: <Constellation />,
  },
];

export default function Sequence() {
  return (
    <div>
      {PANELS.map((p, i) => (
        <section key={p.label} className="border-t border-panel-border">
          <div className="shell grid min-h-[74vh] items-center gap-12 py-20 md:grid-cols-2">
            <Reveal className={i % 2 === 1 ? "md:order-2" : ""}>
              <p className="eyebrow">{p.label}</p>
              <h2 className="h-display mt-5 text-[clamp(2.5rem,6vw,4.5rem)] text-ink">
                {p.statement}
              </h2>
              <p className="mt-6 max-w-md text-lg leading-relaxed text-ink-dim">{p.sub}</p>
            </Reveal>
            <Reveal delay={120} className={`flex items-center justify-center ${i % 2 === 1 ? "md:order-1" : ""}`}>
              {p.visual}
            </Reveal>
          </div>
        </section>
      ))}
    </div>
  );
}
