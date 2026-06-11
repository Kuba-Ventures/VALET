import Link from "next/link";
import Orb from "./Orb";

export default function Hero() {
  return (
    <section className="relative min-h-[92vh] overflow-hidden">
      {/* Full-bleed ambient orb behind the hero */}
      <div className="pointer-events-none absolute inset-0">
        <Orb />
      </div>

      <div className="shell relative flex min-h-[92vh] flex-col justify-center pt-28 pb-24">
        <div className="max-w-2xl">
          <p className="eyebrow">Voice-first control for macOS</p>

          <h1 className="h-display mt-6 text-[clamp(2.75rem,8vw,6rem)] text-ink">
            Talk to your Mac.
            <br />
            <span className="text-ink-dim">It does the work.</span>
          </h1>

          <p className="mt-7 max-w-lg text-lg leading-relaxed text-ink-dim">
            Say what you want in plain words. VALET reaches into the apps you
            already use and gets it done. Not a list of links, the thing itself.
          </p>

          <div className="mt-9 flex flex-col gap-4 sm:flex-row sm:items-center">
            <Link href="/pricing" className="btn-primary">
              Start free trial
            </Link>
            <a href="#demo" className="btn-ghost">
              See it work
            </a>
          </div>

          <p className="mt-5 font-mono text-xs text-ink-faint">
            $20/month after a 7-day trial · cancel anytime
          </p>
        </div>
      </div>

      {/* Scroll cue */}
      <div className="scroll-cue pointer-events-none absolute bottom-8 left-1/2 -translate-x-1/2 font-mono text-[10px] uppercase tracking-[0.3em] text-ink-faint">
        Scroll
      </div>
    </section>
  );
}
