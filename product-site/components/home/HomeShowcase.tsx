"use client";

import { useEffect, useRef, useState, type ComponentType } from "react";
import Reveal from "../Reveal";
import {
  TalkVisual,
  InstantVisual,
  ControlVisual,
  ShipVisual,
  TeachVisual,
} from "./showcaseVisuals";

/**
 * A card's visual is EITHER a real media file (image or video) shown in the
 * window-framed panel, OR — until you drop one in — the stylized CSS mockup.
 *
 * To use a real example: put the file in `public/showcase/` and set `media`,
 * e.g. media: { type: "video", src: "/showcase/talk.mp4", poster: "/showcase/talk.jpg" }
 *      media: { type: "image", src: "/showcase/instant.png" }
 * Videos autoplay muted + loop (silent product loops, like Raycast).
 */
type Media = { type: "image" | "video"; src: string; poster?: string };

type Card = {
  n: string;
  tab: string;
  title: string;
  body: string;
  Visual: ComponentType;
  media: Media | null;
};

const CARDS: Card[] = [
  {
    n: "01",
    tab: "Talk",
    title: "Talk from anywhere.",
    body: "Hold ⌃⌥ and speak from any app. The orb listens, no window-switching.",
    Visual: TalkVisual,
    media: null,
  },
  {
    n: "02",
    tab: "Instant",
    title: "Instant, no typing.",
    body: "Open apps, find files, jump to settings, run system actions. Sub-second, no model round-trip.",
    Visual: InstantVisual,
    media: null,
  },
  {
    n: "03",
    tab: "Control",
    title: "It actually does it.",
    body: "VALET glides your cursor and clicks for you. Native Mac control with a confirm card, kill switch, and Escape to stop.",
    Visual: ControlVisual,
    media: null,
  },
  {
    n: "04",
    tab: "Ship",
    title: "Ship it by voice.",
    body: "Dictate to Claude Code and Cursor and spin up whole projects without touching the keyboard.",
    Visual: ShipVisual,
    media: null,
  },
  {
    n: "05",
    tab: "Teach",
    title: "Or it shows you how.",
    body: "Guided walkthroughs glide your cursor to each step and wait. It teaches, it doesn't take over.",
    Visual: TeachVisual,
    media: null,
  },
];

/** The framed "screen" that holds either a real media example or the mockup. */
function CardScreen({ media, Visual }: { media: Media | null; Visual: ComponentType }) {
  return (
    <div className="lp-card-screen">
      <div className="lp-card-screen-bar" aria-hidden="true">
        <i /><i /><i />
      </div>
      <div className="lp-card-screen-body">
        {media?.type === "video" ? (
          <video
            className="lp-card-media"
            autoPlay
            muted
            loop
            playsInline
            poster={media.poster}
          >
            <source src={media.src} />
          </video>
        ) : media?.type === "image" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="lp-card-media" src={media.src} alt="" />
        ) : (
          <Visual />
        )}
      </div>
    </div>
  );
}

/**
 * Raycast-style horizontal showcase: near-full-width cards with a media/visual
 * panel per capability, the next card peeking at the edge, prev/next arrows, and
 * a bottom pill tab-strip to jump between cards. Scroll-snap drives active state.
 */
export default function HomeShowcase() {
  const trackRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const cards = Array.from(track.querySelectorAll<HTMLElement>(".lp-card"));
    const io = new IntersectionObserver(
      (entries) => {
        let best: { i: number; ratio: number } | null = null;
        for (const e of entries) {
          const i = cards.indexOf(e.target as HTMLElement);
          if (i < 0) continue;
          if (!best || e.intersectionRatio > best.ratio) best = { i, ratio: e.intersectionRatio };
        }
        if (best && best.ratio > 0.5) setActive(best.i);
      },
      { root: track, threshold: [0.5, 0.75, 1] },
    );
    cards.forEach((c) => io.observe(c));
    return () => io.disconnect();
  }, []);

  function go(i: number) {
    const track = trackRef.current;
    if (!track) return;
    const clamped = Math.max(0, Math.min(CARDS.length - 1, i));
    const card = track.querySelectorAll<HTMLElement>(".lp-card")[clamped];
    card?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }

  return (
    <section className="lp-showcase" aria-roledescription="carousel" aria-label="What VALET does">
      <div className="shell">
        <Reveal>
          <p className="eyebrow">What it does</p>
          <h2 className="h-display mt-5 max-w-3xl text-[clamp(2rem,5vw,3.25rem)] text-ink">
            Speed is the product.
          </h2>
        </Reveal>
      </div>

      <div className="lp-showcase-track" ref={trackRef}>
        {CARDS.map((c, i) => (
          <article
            key={c.title}
            className="lp-card"
            aria-roledescription="slide"
            aria-label={`${i + 1} of ${CARDS.length}: ${c.title}`}
          >
            <div className="lp-card-body">
              <span className="lp-feature-num">{c.n}</span>
              <h3 className="h-display lp-card-title text-ink">{c.title}</h3>
              <p className="lp-card-text">{c.body}</p>
            </div>
            <div className="lp-card-visual">
              <CardScreen media={c.media} Visual={c.Visual} />
            </div>
          </article>
        ))}
      </div>

      <div className="shell lp-showcase-nav">
        <button
          type="button"
          className="lp-nav-arrow"
          onClick={() => go(active - 1)}
          disabled={active === 0}
          aria-label="Previous"
        >
          ←
        </button>
        <div className="lp-tabstrip" role="tablist" aria-label="Jump to capability">
          {CARDS.map((c, i) => (
            <button
              key={c.tab}
              type="button"
              role="tab"
              aria-selected={i === active}
              className={`lp-tab ${i === active ? "is-active" : ""}`}
              onClick={() => go(i)}
            >
              {c.tab}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="lp-nav-arrow"
          onClick={() => go(active + 1)}
          disabled={active === CARDS.length - 1}
          aria-label="Next"
        >
          →
        </button>
      </div>
    </section>
  );
}
