"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import HeroOrb from "./HeroOrb";

const HERO_COPY: Record<
  string,
  { title2: string; sub: string; cta?: string }
> = {
  automation: {
    title2: "the word.",
    sub: "Stop wasting time on repetitive tasks. Our AI does the work for you.",
  },
  dev: {
    title2: "the word.",
    sub: "Dispatch coding tasks to Claude Code by voice — stay in flow.",
  },
  founder: {
    title2: "the word.",
    sub: "Run your calendar, mail and notes just by talking. Meet VALET, your AI butler.",
  },
  productivity: {
    title2: "the word.",
    sub: "Automate any Mac app by voice — no scripts required.",
  },
};

const DEFAULT_COPY = {
  title2: "the word.",
  sub: "A British-butler voice assistant for macOS that finds files, controls apps, and ships code by voice, from any app. No API keys. Signed & notarized.",
  cta: "Download for Mac",
};

function trackDownload(utmContent: string | null) {
  if (typeof window === "undefined") return;
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push({
    event: "campaign_download_click",
    utm_content: utmContent || "none",
  });
}

function HeroInner({ downloadHref }: { downloadHref: string }) {
  const params = useSearchParams();
  const utmContent = params.get("utm_content");
  const copy = (utmContent && HERO_COPY[utmContent]) || DEFAULT_COPY;

  return (
    <section className="lp-hero">
      <div className="lp-hero-orb-wrap" aria-hidden="true">
        <div className="lp-hero-bloom" />
        <HeroOrb />
      </div>
      <div className="lp-hero-scrim" aria-hidden="true" />

      <div className="shell lp-hero-inner">
        <h1 className="h-display lp-hero-title text-ink">
          Just say
          <br />
          <span className="line-2">{copy.title2}</span>
        </h1>

        <p className="lp-hero-sub">{copy.sub}</p>

        <div className="lp-hero-cta">
          <Link
            href={downloadHref}
            className="btn-primary"
            onClick={() => trackDownload(utmContent)}
          >
            {copy.cta}
          </Link>
          <Link href="/how-it-works" className="lp-hero-seework">
            See it work →
          </Link>
        </div>
      </div>
    </section>
  );
}

export default function HomeHero({ downloadHref }: { downloadHref: string }) {
  return (
    <Suspense>
      <HeroInner downloadHref={downloadHref} />
    </Suspense>
  );
}
