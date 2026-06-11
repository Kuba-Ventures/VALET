"use client";

import { useMemo } from "react";

/**
 * Visual 01: a breathing voice signal. ~34 thin cyan bars, each animating its
 * scaleY on a staggered loop so it reads like live speech.
 */
export default function Waveform() {
  const bars = useMemo(
    () =>
      Array.from({ length: 34 }, () => ({
        h: 30 + Math.random() * 70, // px max height
        delay: -(Math.random() * 1.1).toFixed(2) + "s",
        dur: (0.8 + Math.random() * 0.9).toFixed(2) + "s",
      })),
    [],
  );

  return (
    <div className="wf" aria-hidden>
      {bars.map((b, i) => (
        <span
          key={i}
          className="wf-bar"
          style={{ height: `${b.h}px`, animationDelay: b.delay, animationDuration: b.dur }}
        />
      ))}
    </div>
  );
}
