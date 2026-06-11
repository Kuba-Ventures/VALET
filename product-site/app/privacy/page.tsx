export const metadata = {
  title: "VALET: Privacy",
  description: "What VALET can access, what leaves your Mac, and what we never collect.",
};

export default function PrivacyPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell max-w-3xl">
        <p className="eyebrow">Privacy</p>
        <h1 className="mt-4 h-display text-4xl md:text-6xl text-ink">Privacy.</h1>
        <p className="mt-4 text-ink-faint">Last updated: 2026-06-11</p>

        <p className="mt-8 text-ink-dim leading-relaxed">
          VALET runs on your own Mac. It can see your files, calendar, mail (read-only),
          notes, and screen context, and it acts across your apps. Because of that, we
          are deliberate about what ever leaves your machine.
        </p>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">What stays on your Mac</h2>
        <ul className="list-disc pl-5 text-ink-dim space-y-2">
          <li>Your files, screen contents, and the bodies of your messages and notes.</li>
          <li>
            Your preferences, memory, and settings (local SQLite plus a local{" "}
            <code>.env</code>).
          </li>
          <li>Mail is read-only by design. VALET never sends, deletes, or edits mail.</li>
        </ul>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">What leaves your Mac</h2>
        <ul className="list-disc pl-5 text-ink-dim space-y-2">
          <li>
            <strong>AI and voice requests</strong> go to our hosted proxy, authenticated by
            your license key. We meter usage (request counts, token counts, estimated cost)
            per license to enforce fair use. Request and response <em>contents</em> are not
            stored by default; tracing payloads are scrubbed.
          </li>
          <li>
            <strong>Error reports</strong> are <strong>on by default</strong> so we can fix
            what breaks, and you can turn them off anytime in Settings. We send error{" "}
            <em>metadata</em> only (error type, the module, the action that failed), never
            file contents, message bodies, prompts, or screen data.
          </li>
          <li>
            <strong>Billing</strong> is handled by Stripe; we store a license record (status,
            plan, period) keyed to your subscription.
          </li>
        </ul>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">What we never collect</h2>
        <ul className="list-disc pl-5 text-ink-dim space-y-2">
          <li>The contents of your files, messages, notes, or screen.</li>
          <li>Your prompts or VALET&apos;s replies (beyond per-license usage counts).</li>
        </ul>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Your controls</h2>
        <ul className="list-disc pl-5 text-ink-dim space-y-2">
          <li>
            Telemetry (scrubbed crash reports) is on by default and can be turned off at any
            time in Settings.
          </li>
          <li>Destructive actions always ask first; deletes go to the Trash.</li>
          <li>A global STOP halts any in-progress action.</li>
        </ul>

        <h2 className="h-display text-xl text-ink mt-12 mb-4">Contact</h2>
        <p className="mt-4 text-ink-dim leading-relaxed">
          Questions: reach out via the address on the marketing site.
        </p>
      </div>
    </main>
  );
}
