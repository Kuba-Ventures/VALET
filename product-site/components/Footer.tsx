export default function Footer() {
  return (
    <footer className="border-t border-panel-border">
      <div className="mx-auto flex max-w-shell flex-col items-start justify-between gap-6 px-6 py-12 md:flex-row md:items-center">
        <div>
          <div className="font-mono text-lg font-medium tracking-tight">
            [JARVIS]
          </div>
          <p className="mt-1 text-sm text-ink-faint">
            Voice-first computer control for macOS.
          </p>
        </div>

        <nav className="flex flex-wrap gap-x-8 gap-y-2 text-sm text-ink-dim">
          <a href="#how" className="hover:text-ink">
            How it works
          </a>
          <a href="#pricing" className="hover:text-ink">
            Pricing
          </a>
          <a href="mailto:hello@example.com" className="hover:text-ink">
            Contact
          </a>
        </nav>
      </div>

      <div className="border-t border-panel-border">
        <div className="mx-auto max-w-shell px-6 py-5 text-xs text-ink-faint">
          &copy; {new Date().getFullYear()} [JARVIS]. All rights reserved.
        </div>
      </div>
    </footer>
  );
}
