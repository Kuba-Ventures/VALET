import {
  getAllAccounts,
  getWaitlist,
  getUsageInsights,
  priceForPlan,
  type AdminAccount,
} from "@/lib/admin";
import { isAdminAuthed } from "@/lib/admin-auth";
import { login, logout } from "./actions";
import { TargetIcon } from "./TargetIcon";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Human-readable names for the internal action codes shown in "Top actions".
// The raw code is still shown beside the label for reference.
const ACTION_LABELS: Record<string, string> = {
  open_app: "Open app",
  open_url: "Open website",
  open_project: "Open project",
  open_terminal: "Open terminal",
  browse: "Browse the web",
  research: "Research a question",
  build: "Build (Claude Code)",
  prompt_project: "Work on a project",
  dispatch_to_agent: "Dispatch to sub-agent",
  start_design: "Design session",
  walkthrough: "Guided walkthrough",
  ui_act: "On-screen click / type",
  ui_open: "Open an on-screen item",
  ui_task: "Multi-step on-screen task",
  check_weather: "Weather",
  world_time: "World clock",
  check_calendar: "Check calendar",
  check_mail: "Check mail",
  check_tasks: "Check tasks",
  check_dispatch: "Check build status",
  compose_slack: "Compose Slack message",
  compose_text: "Compose text message",
  summarize_screen: "Summarize the screen",
  describe_screen: "Describe the screen",
};

function actionLabel(code: string): string {
  return (
    ACTION_LABELS[code] ??
    code.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

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
  const warn = status === "past_due";
  return (
    <span
      className={`label-mono rounded-full border px-2.5 py-0.5 ${
        good
          ? "border-accent/40 text-accent"
          : warn
            ? "border-[#ffb86b]/40 text-[#ffb86b]"
            : "border-panel-border text-ink-faint"
      }`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel p-5">
      <div className="text-2xl font-bold tracking-tight">{value}</div>
      <div className="label-mono mt-1">{label}</div>
    </div>
  );
}

function LoginGate({ error }: { error: boolean }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-24">
      <form action={login} className="panel w-full max-w-md p-8 shadow-glow-lg">
        <div className="label-mono mb-3 text-accent/80">Admin</div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard access</h1>
        <p className="mt-3 leading-relaxed text-ink-dim">
          Enter the admin password to view waitlist, subscribers, and usage.
        </p>

        <label htmlFor="admin-password" className="label-mono mt-6 block">
          Password
        </label>
        <input
          id="admin-password"
          name="password"
          type="password"
          required
          autoFocus
          autoComplete="current-password"
          placeholder="••••••••"
          className="input-field mt-2"
        />

        {error && (
          <p className="mt-3 text-sm text-[#ff6b6b]">Incorrect password.</p>
        )}

        <button type="submit" className="btn-primary mt-6 w-full">
          Enter
        </button>
      </form>
    </main>
  );
}

export default async function AdminDashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  if (!(await isAdminAuthed())) {
    return <LoginGate error={error === "1"} />;
  }

  const [waitlist, accounts, insights] = await Promise.all([
    getWaitlist(),
    getAllAccounts(),
    getUsageInsights(),
  ]);

  const active = accounts.filter((a) => a.status === "active");
  const trialing = accounts.filter((a) => a.status === "trialing");
  const mrr = active.reduce((sum, a) => sum + priceForPlan(a.planLabel), 0);
  const spend = accounts.reduce((sum, a) => sum + a.usage.estimated_cost_usd, 0);
  const maxAction = insights.topActions[0]?.count ?? 0;
  const maxApp = insights.topApps[0]?.count ?? 0;
  const maxSite = insights.topSites[0]?.count ?? 0;

  const renewOrTrial = (a: AdminAccount) =>
    a.status === "trialing" ? a.trialEndsAt : a.currentPeriodEnd;

  return (
    <main className="mx-auto min-h-screen max-w-shell px-6 py-24">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="label-mono text-accent/80">Admin</div>
          <h1 className="mt-1 text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-dim">
            Waitlist, subscribers, and usage at a glance.
          </p>
        </div>
        <form action={logout}>
          <button type="submit" className="btn-ghost !px-4 !py-2 text-sm">
            Sign out
          </button>
        </form>
      </div>

      {/* Top-line numbers */}
      <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Waitlist" value={waitlist.total.toLocaleString()} />
        <StatCard label="Active" value={String(active.length)} />
        <StatCard label="On trial" value={String(trialing.length)} />
        <StatCard label="Est. MRR" value={`$${mrr.toLocaleString()}`} />
        <StatCard label="Period spend" value={`$${spend.toFixed(2)}`} />
        <StatCard
          label="Tasks run"
          value={insights.totalTasks.toLocaleString()}
        />
      </div>

      {/* Usage insights */}
      <section className="mt-12">
        <h2 className="text-xl font-bold tracking-tight">What people use most</h2>
        <p className="mt-1 text-sm text-ink-dim">
          Aggregated across {insights.syncedAccounts} synced{" "}
          {insights.syncedAccounts === 1 ? "install" : "installs"}. No prompts or
          content — just action counts, the apps and site domains opened, and
          which connections are working.
        </p>

        {/* What kinds of requests — internal action codes, friendly-labeled */}
        <div className="panel mt-6 p-6">
          <div className="label-mono mb-4">Top actions</div>
          {insights.topActions.length === 0 ? (
            <p className="text-sm text-ink-dim">No usage synced yet.</p>
          ) : (
            <ul className="space-y-3">
              {insights.topActions.slice(0, 12).map((a) => (
                <li key={a.action}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="flex min-w-0 items-baseline gap-2">
                      <span className="truncate text-ink">
                        {actionLabel(a.action)}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-ink-dim/50">
                        {a.action}
                      </span>
                    </span>
                    <span className="shrink-0 text-ink-dim">
                      {a.count.toLocaleString()}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-panel-border/40">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{
                        width: `${maxAction ? (a.count / maxAction) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Concrete breakdowns — which apps / sites (above connections) */}
        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          {(
            [
              ["Top apps", "app", insights.topApps, maxApp],
              ["Top websites", "site", insights.topSites, maxSite],
            ] as const
          ).map(([title, kind, items, max]) => (
            <div key={title} className="panel p-6">
              <div className="label-mono mb-4">{title}</div>
              {items.length === 0 ? (
                <p className="text-sm text-ink-dim">No usage synced yet.</p>
              ) : (
                <ul className="space-y-3">
                  {items.map((it) => (
                    <li key={it.label}>
                      <div className="flex items-center justify-between gap-3 text-sm">
                        <span className="flex min-w-0 items-center gap-2">
                          <TargetIcon kind={kind} label={it.label} />
                          <span className="truncate text-ink">{it.label}</span>
                        </span>
                        <span className="shrink-0 text-ink-dim">
                          {it.count.toLocaleString()}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-panel-border/40">
                        <div
                          className="h-full rounded-full bg-accent"
                          style={{
                            width: `${max ? (it.count / max) * 100 : 0}%`,
                          }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>

        {/* Connections last */}
        <div className="panel mt-6 p-6">
          <div className="label-mono mb-4">Connections in use</div>
          <ul className="space-y-4">
            {(
              [
                ["Calendar", insights.connections.calendar],
                ["Mail", insights.connections.mail],
                ["Notes", insights.connections.notes],
              ] as const
            ).map(([label, n]) => {
              const pct = insights.syncedAccounts
                ? Math.round((n / insights.syncedAccounts) * 100)
                : 0;
              return (
                <li key={label}>
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="text-ink">{label}</span>
                    <span className="text-ink-dim">
                      {n} ({pct}%)
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-panel-border/40">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </section>

      {/* Subscribers */}
      <section className="mt-12">
        <h2 className="text-xl font-bold tracking-tight">Subscribers</h2>
        <p className="mt-1 text-sm text-ink-dim">
          {accounts.length} license{accounts.length === 1 ? "" : "s"} total.
          Billing source of truth is Stripe; tier and MRR are derived from the
          plan price id.
        </p>

        <div className="panel mt-6 overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-panel-border text-ink-faint">
                <th className="label-mono px-5 py-3 font-normal">Customer</th>
                <th className="label-mono px-5 py-3 font-normal">Tier</th>
                <th className="label-mono px-5 py-3 font-normal">Status</th>
                <th className="label-mono px-5 py-3 font-normal">
                  Renews / Trial
                </th>
                <th className="label-mono px-5 py-3 font-normal">Joined</th>
                <th className="label-mono px-5 py-3 text-right font-normal">
                  Requests
                </th>
                <th className="label-mono px-5 py-3 text-right font-normal">
                  Est. cost
                </th>
                <th className="label-mono px-5 py-3 font-normal">Account</th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-5 py-8 text-center text-ink-dim">
                    No licenses yet.
                  </td>
                </tr>
              ) : (
                accounts.map((a, i) => (
                  <tr
                    key={i}
                    className="border-b border-panel-border/50 last:border-0"
                  >
                    <td className="px-5 py-3">
                      {a.customerEmail ?? (
                        <span className="text-ink-faint">unknown</span>
                      )}
                    </td>
                    <td className="px-5 py-3">{a.planLabel}</td>
                    <td className="px-5 py-3">
                      <StatusBadge status={a.status} />
                    </td>
                    <td className="px-5 py-3 text-ink-dim">
                      {formatDate(renewOrTrial(a))}
                    </td>
                    <td className="px-5 py-3 text-ink-dim">
                      {formatDate(a.createdAt)}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {a.usage.requests.toLocaleString()}
                    </td>
                    <td className="px-5 py-3 text-right">
                      ${a.usage.estimated_cost_usd.toFixed(2)}
                    </td>
                    <td className="px-5 py-3">
                      {a.claimed ? (
                        <span className="text-accent">Linked</span>
                      ) : (
                        <span className="text-ink-faint">Unclaimed</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Waitlist */}
      <section className="mt-12">
        <h2 className="text-xl font-bold tracking-tight">Waitlist</h2>
        <p className="mt-1 text-sm text-ink-dim">
          {waitlist.total.toLocaleString()} signup
          {waitlist.total === 1 ? "" : "s"}
          {waitlist.recent.length < waitlist.total
            ? `, showing the latest ${waitlist.recent.length}`
            : ""}
          .
        </p>

        <div className="panel mt-6 overflow-x-auto">
          <table className="w-full min-w-[480px] text-left text-sm">
            <thead>
              <tr className="border-b border-panel-border text-ink-faint">
                <th className="label-mono px-5 py-3 font-normal">Email</th>
                <th className="label-mono px-5 py-3 font-normal">Source</th>
                <th className="label-mono px-5 py-3 font-normal">Joined</th>
              </tr>
            </thead>
            <tbody>
              {waitlist.recent.length === 0 ? (
                <tr>
                  <td colSpan={3} className="px-5 py-8 text-center text-ink-dim">
                    No signups yet.
                  </td>
                </tr>
              ) : (
                waitlist.recent.map((w, i) => (
                  <tr
                    key={i}
                    className="border-b border-panel-border/50 last:border-0"
                  >
                    <td className="px-5 py-3">{w.email}</td>
                    <td className="px-5 py-3 text-ink-dim">
                      {w.source ?? "—"}
                    </td>
                    <td className="px-5 py-3 text-ink-dim">
                      {formatDate(w.createdAt)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
