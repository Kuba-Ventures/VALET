"use client";

import { useState } from "react";

type Qa = { q: string; a: string };

const FAQS: Qa[] = [
  {
    q: "What does it run on?",
    a: "VALET is built for macOS. You download the app, sign in, and grant it permission to work across your apps and files.",
  },
  {
    q: "What does the free trial include?",
    a: "The full Pro plan for 7 days. Unlimited voice control across your apps, fast and deep work, all of it. No charge until the trial ends, and you can cancel before then.",
  },
  {
    q: "How do I cancel?",
    a: "One click in settings, any time. Your plan stays active through the period you’ve already paid for.",
  },
  {
    q: "Do I need my own API key?",
    a: "No. Every paid plan includes the intelligence behind VALET. There’s nothing to wire up and no model to bring.",
  },
  {
    q: "What about my privacy?",
    a: "VALET acts on your machine with the permissions you grant, and you can see and revoke them at any time. It does only what you ask, when you ask.",
  },
];

export default function FaqAccordion() {
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  return (
    <div className="mx-auto max-w-3xl border-t border-panel-border">
      {FAQS.map((f, i) => {
        const isOpen = openIndex === i;
        return (
          <div key={f.q} className={`faq-item${isOpen ? " open" : ""}`}>
            <button
              type="button"
              className="faq-q"
              aria-expanded={isOpen}
              onClick={() => setOpenIndex(isOpen ? null : i)}
            >
              {f.q}
              <span className="faq-plus" aria-hidden>
                +
              </span>
            </button>
            <div className="faq-a">{f.a}</div>
          </div>
        );
      })}
    </div>
  );
}
