import { cookies } from "next/headers";
import { createHash, timingSafeEqual } from "node:crypto";

/**
 * Simple shared-password gate for the standalone /admin dashboard.
 *
 * This is deliberately lightweight ("for now"): a single password, set via the
 * ADMIN_PANEL_PASSWORD env var (default "VALETADMIN"). It is SEPARATE from the
 * owner-only /account/admin view, which is gated per-user by ADMIN_EMAILS.
 *
 * Security posture, honestly stated:
 *  - One shared secret, no per-user identity, no audit trail.
 *  - The cookie stores a SHA-256 of the password, never the password itself,
 *    and is httpOnly + sameSite=strict so page scripts can't read it and it
 *    doesn't ride along on cross-site requests.
 *  - Rotating ADMIN_PANEL_PASSWORD invalidates every existing session cookie
 *    (the stored hash no longer matches).
 *  - The password never reaches the browser and is compared in constant time.
 * Replace with the ADMIN_EMAILS/session gate before this holds anything more
 * sensitive than read-only counts.
 */

const COOKIE_NAME = "valet_admin";

function adminPassword(): string {
  return process.env.ADMIN_PANEL_PASSWORD ?? "VALETADMIN";
}

/** The opaque token we store in the cookie: a hash of the current password. */
function expectedToken(): string {
  return createHash("sha256").update(adminPassword()).digest("hex");
}

function constantTimeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

/** True when the request carries a valid admin session cookie. */
export async function isAdminAuthed(): Promise<boolean> {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  if (!token) return false;
  return constantTimeEqual(token, expectedToken());
}

/** Verify a submitted password (constant time). Does not set anything. */
export function passwordMatches(submitted: string): boolean {
  return constantTimeEqual(submitted, adminPassword());
}

/** Establish an admin session by setting the signed token cookie. */
export async function setAdminSession(): Promise<void> {
  (await cookies()).set(COOKIE_NAME, expectedToken(), {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 12, // 12 hours
  });
}

/** Tear down the admin session. */
export async function clearAdminSession(): Promise<void> {
  (await cookies()).delete(COOKIE_NAME);
}
