import Link from "next/link";
import { redirect } from "next/navigation";
import { createSupabaseServerClient } from "@/lib/auth/server";
import {
  getAccountLicenses,
  getLatestAccountSync,
  linkLicensesByEmail,
  type AccountLicense,
  type AccountSync,
} from "@/lib/account";
import { isAdmin } from "@/lib/admin";
import CopyButton from "@/components/CopyButton";
import SignOutButton from "@/components/account/SignOutButton";
import ManageBillingButton from "@/components/account/ManageBillingButton";
import ClaimLicenseForm from "@/components/account/ClaimLicenseForm";
import DeviceSettingsCard from "@/components/account/DeviceSettingsCard";

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
            {Math.round(pct)}%
          </div>
          {/* Percentage only — never expose the dollar spend or the limit to
              the customer. The raw figures stay server-side (this is a server
              component, so they never reach the browser). */}
          <div className="label-mono mt-1">Monthly usage</div>
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

function timeAgo(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "—";
  }
}

const PROFILE_FIELDS: { key: keyof NonNullable<AccountSync["profile"]>; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "honorific", label: "Honorific" },
  { key: "date_of_birth", label: "Date of birth" },
  { key: "location", label: "Location" },
  { key: "work_email", label: "Work email" },
  { key: "personal_email", label: "Personal email" },
];

function ProfileSection({ profile }: { profile: NonNullable<AccountSync["profile"]> }) {
  const rows = PROFILE_FIELDS.filter((f) => profile[f.key]);
  if (!rows.length) return null;
  return (
    <div className="panel p-6 sm:p-8">
      <div className="label-mono mb-4">Your profile</div>
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {rows.map((f) => (
          <div key={f.key}>
            <dt className="label-mono mb-1">{f.label}</dt>
            <dd className="text-sm text-ink">{profile[f.key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function StatsSection({ stats }: { stats: NonNullable<AccountSync["stats"]> }) {
  const hasNumbers =
    stats.total_tasks != null ||
    stats.success_rate != null ||
    stats.avg_duration_seconds != null;
  const top = stats.top_actions ?? [];
  if (!hasNumbers && !top.length) return null;
  return (
    <div className="panel p-6 sm:p-8">
      <div className="label-mono mb-4">Speech &amp; activity</div>
      {hasNumbers && (
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-2xl font-bold tracking-tight">
              {(stats.total_tasks ?? 0).toLocaleString()}
            </div>
            <div className="label-mono mt-1">Tasks</div>
          </div>
          <div>
            <div className="text-2xl font-bold tracking-tight">
              {Math.round(stats.success_rate ?? 0)}%
            </div>
            <div className="label-mono mt-1">Success</div>
          </div>
          <div>
            <div className="text-2xl font-bold tracking-tight">
              {(stats.avg_duration_seconds ?? 0).toFixed(1)}s
            </div>
            <div className="label-mono mt-1">Avg. time</div>
          </div>
        </div>
      )}
      {top.length > 0 && (
        <div className="mt-6 border-t border-panel-border pt-5">
          <div className="label-mono mb-3">Top requests</div>
          <ul className="flex flex-col gap-2">
            {top.map((a, i) => (
              <li key={i} className="flex items-center justify-between text-sm">
                <span className="text-ink">{a.action}</span>
                <span className="font-mono text-ink-dim">{a.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const CONNECTION_LABELS: { key: keyof NonNullable<AccountSync["connections"]>; label: string }[] = [
  { key: "calendar", label: "Calendar" },
  { key: "mail", label: "Mail" },
  { key: "notes", label: "Notes" },
];

function ConnectionsSection({
  connections,
}: {
  connections: NonNullable<AccountSync["connections"]>;
}) {
  return (
    <div className="panel p-6 sm:p-8">
      <div className="label-mono mb-4">Connected apps</div>
      <div className="flex flex-wrap gap-3">
        {CONNECTION_LABELS.map((c) => {
          const on = connections[c.key] === true;
          return (
            <span
              key={c.key}
              className={`label-mono rounded-full border px-3 py-1.5 ${
                on
                  ? "border-accent/40 text-accent"
                  : "border-panel-border text-ink-faint"
              }`}
            >
              {c.label} {on ? "· on" : "· off"}
            </span>
          );
        })}
      </div>
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
  const sync = licenses.length ? await getLatestAccountSync(user.id) : null;

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
          <div className="flex items-center gap-3">
            {isAdmin(user.email) && (
              <Link
                href="/account/admin"
                className="btn-ghost !px-4 !py-2 text-sm"
              >
                Admin
              </Link>
            )}
            <SignOutButton />
          </div>
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

              {/* Web-controlled settings the app applies (Phase 2). */}
              <DeviceSettingsCard />

              {/* Synced from the desktop app via /api/proxy/sync. */}
              {sync ? (
                <>
                  {sync.profile && <ProfileSection profile={sync.profile} />}
                  {sync.stats && <StatsSection stats={sync.stats} />}
                  {sync.connections && (
                    <ConnectionsSection connections={sync.connections} />
                  )}
                  <p className="text-xs text-ink-faint">
                    Synced from your app {timeAgo(sync.updatedAt)}
                    {sync.appVersion ? ` · v${sync.appVersion}` : ""}.
                  </p>
                </>
              ) : (
                <div className="panel p-6 text-sm text-ink-faint">
                  <div className="label-mono mb-2">Waiting for your app</div>
                  Your profile, speech stats and connected apps (Calendar, Mail,
                  Notes) will appear here the first time your VALET app syncs.
                </div>
              )}

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
