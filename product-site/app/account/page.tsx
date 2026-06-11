import Link from "next/link";
import { redirect } from "next/navigation";
import { createSupabaseServerClient } from "@/lib/auth/server";
import {
  getAccountLicenses,
  linkLicensesByEmail,
  type AccountLicense,
} from "@/lib/account";
import CopyButton from "@/components/CopyButton";
import SignOutButton from "@/components/account/SignOutButton";
import ManageBillingButton from "@/components/account/ManageBillingButton";
import ClaimLicenseForm from "@/components/account/ClaimLicenseForm";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "—";
  }
}

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  trialing: "Trial",
  past_due: "Past due",
  canceled: "Canceled",
  invalid: "Inactive",
};

function StatusBadge({ status }: { status: string }) {
  const good = status === "active" || status === "trialing";
  return (
    <span
      className={`label-mono rounded-full border px-3 py-1 ${
        good
          ? "border-accent/40 text-accent"
          : "border-panel-border text-ink-faint"
      }`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

function UsagePanel({ usage }: { usage: AccountLicense["usage"] }) {
  const pct = Math.max(
    0,
    Math.min(100, (usage.estimated_cost_usd / usage.allowance_usd) * 100),
  );
  const tokens = usage.input_tokens + usage.output_tokens;
  return (
    <div className="mt-6 border-t border-panel-border pt-5">
      <div className="label-mono mb-3">This period</div>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <div className="text-2xl font-bold tracking-tight">
            {usage.requests.toLocaleString()}
          </div>
          <div className="label-mono mt-1">Voice requests</div>
        </div>
        <div>
          <div className="text-2xl font-bold tracking-tight">
            {tokens.toLocaleString()}
          </div>
          <div className="label-mono mt-1">Tokens</div>
        </div>
        <div>
          <div className="text-2xl font-bold tracking-tight">
            ${usage.estimated_cost_usd.toFixed(2)}
          </div>
          <div className="label-mono mt-1">
            of ${usage.allowance_usd.toFixed(0)} fair-use
          </div>
        </div>
      </div>
      <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-bg">
        <div
          className="h-full rounded-full bg-gradient-to-r from-[#6cecff] to-accent"
          style={{ width: `${pct}%` }}
        />
      </div>
      {usage.period_start && (
        <p className="mt-2 text-xs text-ink-faint">
          Resets {formatDate(usage.period_start)} + {usage.period_days} days.
        </p>
      )}
    </div>
  );
}

function LicenseCard({ license }: { license: AccountLicense }) {
  return (
    <div className="panel p-6 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold tracking-tight">
            {license.planLabel}
          </span>
          <StatusBadge status={license.status} />
        </div>
        {license.hasBilling && <ManageBillingButton />}
      </div>

      <div className="mt-6">
        <div className="label-mono mb-2">License key</div>
        <div className="flex items-center gap-3">
          <code className="flex-1 overflow-x-auto rounded-md border border-panel-border bg-bg px-4 py-3 font-mono text-sm text-accent">
            {license.licenseKey}
          </code>
          <CopyButton value={license.licenseKey} />
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-4 text-sm">
        <div>
          <div className="label-mono mb-1">
            {license.status === "trialing" ? "Trial ends" : "Renews"}
          </div>
          <div className="text-ink">
            {formatDate(
              license.status === "trialing"
                ? license.trialEndsAt
                : license.currentPeriodEnd,
            )}
          </div>
        </div>
        <div>
          <div className="label-mono mb-1">App</div>
          <a
            href={`/api/download?key=${encodeURIComponent(license.licenseKey)}`}
            className="text-accent hover:underline"
          >
            Download VALET
          </a>
        </div>
      </div>

      <UsagePanel usage={license.usage} />
    </div>
  );
}

export default async function AccountPage() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/account/login");

  // Lazy auto-claim: attach any license bought with this email.
  await linkLicensesByEmail(user.id, user.email);
  const licenses = await getAccountLicenses(user.id);

  return (
    <main className="mx-auto min-h-screen max-w-shell px-6 py-24">
      <div className="mx-auto max-w-2xl">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="label-mono text-accent/80">Account</div>
            <h1 className="mt-1 text-3xl font-bold tracking-tight">
              Your VALET
            </h1>
            <p className="mt-1 text-sm text-ink-dim">{user.email}</p>
          </div>
          <SignOutButton />
        </div>

        <div className="mt-10 flex flex-col gap-6">
          {licenses.length === 0 ? (
            <div className="panel p-8">
              <div className="label-mono mb-2 text-accent/80">No license yet</div>
              <h2 className="text-xl font-bold tracking-tight">
                Link your license
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-ink-dim">
                If you bought VALET with this email it will appear automatically.
                Otherwise paste your key below, or{" "}
                <Link href="/pricing" className="text-accent hover:underline">
                  start a free trial
                </Link>
                .
              </p>
              <div className="mt-6">
                <ClaimLicenseForm />
              </div>
            </div>
          ) : (
            <>
              {licenses.map((l) => (
                <LicenseCard key={l.licenseKey} license={l} />
              ))}

              {/* Phase 2: speech stats, profile and connections sync up from the
                  desktop app and surface here. */}
              <div className="panel p-6 text-sm text-ink-faint">
                <div className="label-mono mb-2">Coming soon</div>
                Speech stats, your profile and connected apps (Calendar, Mail,
                Notes) will appear here once your VALET app syncs them up.
              </div>

              <details className="panel p-6">
                <summary className="cursor-pointer text-sm text-ink-dim hover:text-accent">
                  Link another license key
                </summary>
                <div className="mt-4">
                  <ClaimLicenseForm />
                </div>
              </details>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
