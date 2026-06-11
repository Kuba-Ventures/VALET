export const metadata = {
  title: "VALET: Privacy",
  description: "What VALET can access, what leaves your Mac, and what we never collect.",
};

export default function PrivacyPage() {
  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "64px 24px", lineHeight: 1.6 }}>
      <h1>Privacy</h1>
      <p style={{ opacity: 0.7 }}>Last updated: 2026-06-10</p>

      <p>
        VALET runs on your own Mac. It can see your files, calendar, mail (read-only),
        notes, and screen context, and it acts across your apps. Because of that, we
        are deliberate about what ever leaves your machine.
      </p>

      <h2>What stays on your Mac</h2>
      <ul>
        <li>Your files, screen contents, and the bodies of your messages and notes.</li>
        <li>Your preferences, memory, and settings (local SQLite + a local <code>.env</code>).</li>
        <li>Mail is read-only by design. VALET never sends, deletes, or edits mail.</li>
      </ul>

      <h2>What leaves your Mac</h2>
      <ul>
        <li>
          <strong>AI &amp; voice requests</strong> go to our hosted proxy, authenticated by
          your license key. We meter usage (request counts, token counts, estimated cost)
          per license to enforce fair use. Request and response <em>contents</em> are not
          stored by default; tracing payloads are scrubbed.
        </li>
        <li>
          <strong>Error reports</strong> are <strong>on by default</strong> so we can fix what
          breaks, and you can turn them off anytime in Settings. We send error <em>metadata</em>
          only (error type, the module, the action that failed), never file contents, message
          bodies, prompts, or screen data.
        </li>
        <li>
          <strong>Billing</strong> is handled by Stripe; we store a license record (status,
          plan, period) keyed to your subscription.
        </li>
      </ul>

      <h2>What we never collect</h2>
      <ul>
        <li>The contents of your files, messages, notes, or screen.</li>
        <li>Your prompts or VALET’s replies (beyond per-license usage counts).</li>
      </ul>

      <h2>Your controls</h2>
      <ul>
        <li>Telemetry (scrubbed crash reports) is on by default and can be turned off at any time in Settings.</li>
        <li>Destructive actions always ask first; deletes go to the Trash.</li>
        <li>A global STOP halts any in-progress action.</li>
      </ul>

      <h2>Contact</h2>
      <p>Questions: reach out via the address on the marketing site.</p>
    </main>
  );
}
