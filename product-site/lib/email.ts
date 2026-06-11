/**
 * Transactional email via the Resend HTTP API (no SDK dependency). Used to send
 * the buyer their license key on purchase, so it never lives only on the success
 * page where a closed tab loses it forever.
 *
 * Graceful no-op until configured: if RESEND_API_KEY or EMAIL_FROM is unset the
 * send is skipped silently, so checkout keeps working before email is wired up.
 * Set EMAIL_FROM to a verified Resend sender, e.g. "VALET <keys@yourdomain.com>".
 */

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://valetvoice.vercel.app";

function licenseEmailHtml(licenseKey: string): string {
  return `
  <div style="background:#05080c;padding:40px 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <div style="max-width:520px;margin:0 auto;background:#0e1521;border:1px solid rgba(120,150,180,0.18);border-radius:16px;padding:36px;">
      <div style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:20px;color:#eaf1f7;letter-spacing:0.02em;">VA<span style="color:#4de3f2;">LET</span></div>
      <h1 style="color:#eaf1f7;font-size:24px;margin:24px 0 8px;">Your license key.</h1>
      <p style="color:#7e8a9c;font-size:15px;line-height:1.6;margin:0 0 24px;">Thanks for starting with VALET. Keep this key safe. You will paste it into the app's setup to unlock everything.</p>
      <div style="background:#05080c;border:1px solid rgba(77,227,242,0.3);border-radius:10px;padding:16px 18px;font-family:'JetBrains Mono',ui-monospace,monospace;font-size:16px;color:#4de3f2;letter-spacing:0.04em;word-break:break-all;">${licenseKey}</div>
      <p style="color:#7e8a9c;font-size:14px;line-height:1.6;margin:24px 0 0;">Open VALET, and on first launch the setup wizard will ask for this key. You can also paste it anytime under Settings.</p>
      <p style="color:#5a6473;font-size:13px;line-height:1.6;margin:24px 0 0;">Need the app? <a href="${SITE}" style="color:#4de3f2;text-decoration:none;">${SITE.replace(/^https?:\/\//, "")}</a></p>
    </div>
  </div>`;
}

export async function sendLicenseEmail(to: string | null | undefined, licenseKey: string): Promise<void> {
  const apiKey = process.env.RESEND_API_KEY;
  const from = process.env.EMAIL_FROM;
  if (!apiKey || !from || !to) return; // not configured (or no email on file): skip silently

  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from,
        to,
        subject: "Your VALET license key",
        html: licenseEmailHtml(licenseKey),
      }),
    });
    if (!res.ok) {
      console.error("license email send failed:", res.status, await res.text().catch(() => ""));
    }
  } catch (err) {
    // Never let an email failure break the webhook; the license is already saved.
    console.error("license email error:", err instanceof Error ? err.message : err);
  }
}
