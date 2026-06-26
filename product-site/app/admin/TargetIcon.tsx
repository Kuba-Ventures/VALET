"use client";

import { useState } from "react";

/**
 * Small icon for an opened app or site, with a colored letter-avatar fallback
 * when the remote icon 404s or fails to load. Icons are fetched only here in
 * the admin dashboard:
 *   - site → favicon service, keyed by the bare domain
 *   - app  → simple-icons CDN, keyed by the lowercased, space-stripped name
 */
export function TargetIcon({
  kind,
  label,
}: {
  kind: "app" | "site";
  label: string;
}) {
  const [failed, setFailed] = useState(false);

  const src =
    kind === "site"
      ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(label)}&sz=64`
      : `https://cdn.simpleicons.org/${encodeURIComponent(
          label.toLowerCase().replace(/\s+/g, ""),
        )}`;

  if (failed) {
    // Deterministic hue from the label so the same app/site is always the
    // same color.
    let h = 0;
    for (let i = 0; i < label.length; i++) {
      h = (h * 31 + label.charCodeAt(i)) % 360;
    }
    return (
      <span
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] font-semibold text-white"
        style={{ backgroundColor: `hsl(${h} 45% 45%)` }}
        aria-hidden
      >
        {(label[0] || "?").toUpperCase()}
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={20}
      height={20}
      className="h-5 w-5 shrink-0 rounded bg-panel-border/30"
      onError={() => setFailed(true)}
    />
  );
}
