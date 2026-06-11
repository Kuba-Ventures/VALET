import Link from "next/link";

const LINKS = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
  { href: "/faq", label: "FAQ" },
  { href: "/contact", label: "Contact" },
];

export default function Footer() {
  return (
    <footer className="border-t border-panel-border">
      <div className="shell grid gap-10 py-16 md:grid-cols-2">
        <div>
          <div className="brand-mark text-2xl">
            VA<span className="let">LET</span>
          </div>
          <p className="mt-3 font-mono text-sm text-ink-faint">
            <span className="let">V</span>oice-<span className="let">A</span>ctivated{" "}
            <span className="let">L</span>ocal <span className="let">E</span>ngineering{" "}
            <span className="let">T</span>erminal
          </p>
          <p className="mt-4 max-w-xs text-ink-dim">
            Voice-first computer control for macOS.
          </p>
        </div>

        <nav className="flex flex-col gap-3 md:items-end">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="nav-link">
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="shell border-t border-panel-border py-6">
        <p className="font-mono text-xs text-ink-faint">
          © 2026 VALET. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
