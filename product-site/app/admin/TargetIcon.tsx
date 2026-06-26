"use client";

import { useState } from "react";

/**
 * Small icon for an opened app or site.
 *
 * - **site**: real favicon via Google's favicon service (sites have domains).
 *   Falls back to a letter badge if the favicon fails to load.
 * - **app**: a colored letter badge. There's no reliable way to fetch an
 *   arbitrary macOS app's icon from a web dashboard, so we render a clean,
 *   consistent initial badge instead of a broken/abstract logo.
 */

function LetterBadge({ label }: { label: string }) {
  // Deterministic hue from the label so the same app/site is always one color.
  let h = 0;
  for (let i = 0; i < label.length; i++) {
    h = (h * 31 + label.charCodeAt(i)) % 360;
  }
  return (
    <span
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold text-white"
      style={{ backgroundColor: `hsl(${h} 50% 45%)` }}
      aria-hidden
    >
      {(label[0] || "?").toUpperCase()}
    </span>
  );
}

export function TargetIcon({
  kind,
  label,
}: {
  kind: "app" | "site";
  label: string;
}) {
  const [failed, setFailed] = useState(false);

  // Apps: no reliable web logo source — always use the letter badge.
  if (kind === "app" || failed) {
    return <LetterBadge label={label} />;
  }

  // Sites: favicon, with a letter-badge fallback on load error.
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(label)}&sz=64`}
      alt=""
      width={20}
      height={20}
      className="h-5 w-5 shrink-0 rounded bg-panel-border/30"
      onError={() => setFailed(true)}
    />
  );
}
