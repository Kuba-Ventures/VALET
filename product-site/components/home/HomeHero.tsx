import Link from "next/link";
import HeroOrb from "./HeroOrb";

/**
 * Landing hero: a live, slowly-rotating violet particle orb + bloom behind a
 * tight headline and the download CTA. The orb is decorative (aria-hidden); the
 * headline is the page's single <h1>. (The push-to-talk shortcut now has its own
 * section — see HomePushToTalk.)
 */
export default function HomeHero({ downloadHref }: { downloadHref: string }) {
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
          <span className="line-2">the word.</span>
        </h1>

        <p className="lp-hero-sub">
          A British-butler voice assistant for macOS that finds files, controls
          apps, and ships code by voice, from any app. No API keys. Signed &amp;
          notarized.
        </p>

        <div className="lp-hero-cta">
          <Link href={downloadHref} className="btn-primary">
            Download for Mac
          </Link>
          <Link href="/how-it-works" className="lp-hero-seework">
            See it work →
          </Link>
        </div>
      </div>
    </section>
  );
}
