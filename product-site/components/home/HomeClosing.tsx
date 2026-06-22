import Link from "next/link";
import Reveal from "../Reveal";

export default function HomeClosing({ downloadHref }: { downloadHref: string }) {
  return (
    <section className="lp-closing">
      <div className="lp-closing-bloom" aria-hidden="true" />
      <div className="shell py-28 lp-closing-inner">
        <Reveal>
          <h2 className="h-display lp-closing-title text-ink">Say the word.</h2>
          <div className="lp-closing-cta">
            <Link href={downloadHref} className="btn-primary">
              Download for Mac
            </Link>
          </div>
          <p className="lp-closing-sub">
            macOS · Apple silicon · signed &amp; notarized · no API keys
          </p>
        </Reveal>
      </div>
    </section>
  );
}
