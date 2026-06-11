"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
  { href: "/faq", label: "FAQ" },
  { href: "/contact", label: "Contact" },
];

export default function Nav() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className={`nav ${scrolled ? "scrolled" : ""}`}>
      <div className="shell flex items-center justify-between py-4">
        <Link href="/" className="brand-mark text-lg">
          VA<span className="let">LET</span>
        </Link>

        <nav className="hidden items-center gap-8 md:flex">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`nav-link ${pathname === l.href ? "active" : ""}`}
            >
              {l.label}
            </Link>
          ))}
          <Link href="/pricing" className="btn-primary !px-5 !py-2 text-sm">
            Start free trial
          </Link>
        </nav>

        {/* Mobile: just the CTA */}
        <Link href="/pricing" className="btn-primary !px-4 !py-2 text-sm md:hidden">
          Start trial
        </Link>
      </div>
    </header>
  );
}
