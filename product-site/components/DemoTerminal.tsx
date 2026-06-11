"use client";

import { useEffect, useRef, useState } from "react";

// PLACEHOLDER choreography, swap this transcript for a real recorded task.
const ASK =
  "Find the contract Maria sent last week and put the signing date on my calendar.";
const STEPS = [
  'Searched Mail, found "Vendor agreement" from Maria, sent Tuesday',
  "Opened the PDF, read the signing date: Thursday, 2:00 PM",
  "Checked Calendar for conflicts, none found",
  'Created event "Sign vendor contract" with the file attached',
];
const RESULT =
  'Done. "Sign vendor contract" is on your calendar for Thursday at 2:00 PM. Want me to set a reminder the morning of?';

export default function DemoTerminal() {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(0); // number of steps revealed
  const [result, setResult] = useState(false);
  const [active, setActive] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e.isIntersecting) return;
        io.disconnect();
        setActive(true);
        const reduce = matchMedia("(prefers-reduced-motion:reduce)").matches;
        if (reduce) {
          setShown(STEPS.length);
          setResult(true);
          return;
        }
        STEPS.forEach((_, i) => setTimeout(() => setShown(i + 1), 500 + i * 600));
        setTimeout(() => setResult(true), 500 + STEPS.length * 600);
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <section id="demo" className="border-t border-panel-border">
      <div className="shell py-24">
        <p className="eyebrow">A single ask, start to finish</p>
        <h2 className="h-display mt-5 max-w-2xl text-[clamp(2rem,5vw,3.5rem)] text-ink">
          Watch one request resolve.
        </h2>
        <p className="mt-5 max-w-xl text-lg leading-relaxed text-ink-dim">
          You say one sentence. VALET breaks it into steps, does each one across
          your apps, and tells you when it&apos;s done.
        </p>

        <div ref={ref} className={`term mt-12 max-w-3xl ${active ? "in" : ""}`}>
          <div className="term-bar">
            <span className="text-ink-dim">VALET</span>
            <span className="flex items-center gap-2 text-accent">
              <span className="term-dot" /> listening
            </span>
          </div>
          <div className="term-body">
            <div className="text-ink">
              <span className="text-accent">›</span> &quot;{ASK}&quot;
            </div>
            <div className="mt-5 flex flex-col gap-3">
              {STEPS.map((s, i) => (
                <div key={i} className={`term-step ${i < shown ? "show" : ""}`}>
                  <span className="term-check">✓</span>
                  <span className="text-ink-dim">{s}</span>
                </div>
              ))}
            </div>
            <div className={`term-result ${result ? "show" : ""}`}>{RESULT}</div>
          </div>
        </div>
      </div>
    </section>
  );
}
