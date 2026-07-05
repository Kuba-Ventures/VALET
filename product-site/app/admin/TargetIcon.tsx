"use client";

import { useState } from "react";

/**
 * Small icon for an opened app or site.
 *
 * - **site**: real favicon, tried from DuckDuckGo first then Google, falling
 *   back to a letter badge if both fail. DuckDuckGo resolves per-subdomain
 *   (so `mail.google.com` yields the Gmail envelope, not the generic Google
 *   "G" that Google's own service returns for that host); Google is the
 *   backstop for the rare domain DuckDuckGo has no icon for.
 * - **app**: a colored letter badge. There's no reliable way to fetch an
 *   arbitrary macOS app's icon from a web dashboard, so we render a clean,
 *   consistent initial badge instead of a broken/abstract logo.
 */

// Favicon sources tried in order for a site domain. Each returns a 20px-ish
// square icon URL. On load error we advance to the next; past the end we drop
// to the letter badge.
const SITE_ICON_SOURCES: ((domain: string) => string)[] = [
  (d) => `https://icons.duckduckgo.com/ip3/${encodeURIComponent(d)}.ico`,
  (d) =>
    `https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&sz=64`,
];

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
  icon,
}: {
  kind: "app" | "site";
  label: string;
  /** For apps: a data URI of the real macOS icon, when we captured one. */
  icon?: string;
}) {
  // Index into SITE_ICON_SOURCES; once it runs past the end we show the badge.
  const [srcIndex, setSrcIndex] = useState(0);
  const [iconFailed, setIconFailed] = useState(false);

  // Apps with a captured icon: render it, letter-badge on decode failure.
  if (kind === "app" && icon && !iconFailed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={icon}
        alt=""
        width={20}
        height={20}
        className="h-5 w-5 shrink-0 rounded bg-panel-border/30"
        onError={() => setIconFailed(true)}
      />
    );
  }

  // Apps without an icon: letter badge (no reliable web logo source).
  // Sites: fall through to the letter badge once every favicon source failed.
  if (kind === "app" || srcIndex >= SITE_ICON_SOURCES.length) {
    return <LetterBadge label={label} />;
  }

  // Sites: try each favicon source in turn, letter-badge on final failure.
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={srcIndex}
      src={SITE_ICON_SOURCES[srcIndex](label)}
      alt=""
      width={20}
      height={20}
      className="h-5 w-5 shrink-0 rounded bg-panel-border/30"
      onError={() => setSrcIndex((i) => i + 1)}
    />
  );
}
