"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/auth/client";

const LINKS = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
  { href: "/faq", label: "FAQ" },
  { href: "/contact", label: "Contact" },
];

export default function Nav() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  // Starts false so server render and first client render match (no hydration
  // mismatch); flips to true in the effect once the session resolves.
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    let unsub: (() => void) | undefined;
    try {
      const supabase = createSupabaseBrowserClient();
      supabase.auth.getUser().then(({ data }) => setAuthed(!!data.user));
      const { data } = supabase.auth.onAuthStateChange((_e, session) =>
        setAuthed(!!session?.user),
      );
      unsub = () => data.subscription.unsubscribe();
    } catch {
      // Auth not configured — stay in the logged-out view.
    }
    return () => unsub?.();
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
          {authed ? (
            <Link href="/account" className="btn-primary !px-5 !py-2 text-sm">
              Account
            </Link>
          ) : (
            <>
              <Link
                href="/account/login"
                className={`nav-link ${pathname === "/account/login" ? "active" : ""}`}
              >
                Log in
              </Link>
              <Link href="/pricing" className="btn-primary !px-5 !py-2 text-sm">
                Start free trial
              </Link>
            </>
          )}
        </nav>

        {/* Mobile: a single CTA — the account for signed-in users, else trial. */}
        <Link
          href={authed ? "/account" : "/pricing"}
          className="btn-primary !px-4 !py-2 text-sm md:hidden"
        >
          {authed ? "Account" : "Start trial"}
        </Link>
      </div>
    </header>
  );
}
