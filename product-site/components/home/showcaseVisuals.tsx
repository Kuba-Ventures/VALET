import HeroOrb from "./HeroOrb";

/**
 * Stylized, on-brand visual mockups for the Raycast-style showcase carousel.
 * All decorative (aria-hidden) — these are CSS/markup illustrations of each
 * capability, meant to be swapped for real app captures later.
 */

export function TalkVisual() {
  // The same live, spinning violet particle orb that sits behind the hero,
  // with a waveform + "Listening" pill overlaid so the card reads as VALET
  // actively listening.
  return (
    <div className="lp-mock lp-mock--talk" aria-hidden="true">
      <HeroOrb />
      <div className="lp-mock-talk-overlay">
        <div className="lp-mock-wave">
          {[0.4, 0.8, 1, 0.6, 0.9, 0.5, 0.75].map((h, i) => (
            <span key={i} style={{ ["--h" as string]: h, animationDelay: `${i * 0.09}s` }} />
          ))}
        </div>
        <div className="lp-mock-pill">Listening…</div>
      </div>
    </div>
  );
}

export function InstantVisual() {
  return (
    <div className="lp-mock lp-mock--cmd" aria-hidden="true">
      <div className="lp-mock-cmdbar">
        <span className="lp-mock-caret" />
        <span>“Open my Downloads folder”</span>
      </div>
      <div className="lp-mock-result">
        <span className="lp-mock-ico">📁</span>
        <span>Downloads</span>
        <span className="lp-mock-enter">↵</span>
      </div>
      <div className="lp-mock-badge">0.28s · no model round-trip</div>
    </div>
  );
}

export function ControlVisual() {
  return (
    <div className="lp-mock lp-mock--control" aria-hidden="true">
      <div className="lp-mock-win">
        <div className="lp-mock-win-bar">
          <i /><i /><i />
        </div>
        <div className="lp-mock-win-body">
          <div className="lp-mock-fakebtn">Send</div>
          <svg className="lp-mock-cursor" viewBox="0 0 24 24" fill="none">
            <path d="M5 3l14 8-6 1.5L9.5 19 5 3z" fill="#f6f4ff" stroke="#0a0820" strokeWidth="1.2" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
      <div className="lp-mock-confirm">
        <span className="lp-mock-confirm-q">Click “Send”?</span>
        <span className="lp-mock-confirm-btns">
          <b>Confirm</b>
          <em>Cancel</em>
        </span>
        <span className="lp-mock-esc">Esc to stop</span>
      </div>
    </div>
  );
}

export function ShipVisual() {
  return (
    <div className="lp-mock lp-mock--term" aria-hidden="true">
      <div className="lp-mock-term-bar">
        <i /><i /><i />
        <span>claude code</span>
      </div>
      <div className="lp-mock-term-body">
        <div className="lp-mock-term-prompt">valet › scaffold a Next.js landing page</div>
        <div className="lp-mock-term-ok">✓ created app/page.tsx</div>
        <div className="lp-mock-term-ok">✓ added components/Hero.tsx</div>
        <div className="lp-mock-term-ok">✓ wired styles + tokens</div>
        <div className="lp-mock-term-run">▍ installing dependencies…</div>
      </div>
    </div>
  );
}

export function TeachVisual() {
  return (
    <div className="lp-mock lp-mock--teach" aria-hidden="true">
      <div className="lp-mock-pane">
        <div className="lp-mock-pane-row" />
        <div className="lp-mock-pane-row lp-mock-pane-row--target">
          <span className="lp-mock-ring" />
        </div>
        <div className="lp-mock-pane-row" />
        <div className="lp-mock-pane-row" />
      </div>
      <div className="lp-mock-tip">
        Step 2 of 5 · click here
      </div>
      <svg className="lp-mock-cursor lp-mock-cursor--teach" viewBox="0 0 24 24" fill="none">
        <path d="M5 3l14 8-6 1.5L9.5 19 5 3z" fill="#f6f4ff" stroke="#0a0820" strokeWidth="1.2" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
