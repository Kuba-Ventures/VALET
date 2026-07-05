import { getSupabaseAdmin, type LicenseStatus } from "./supabase";
import { getUsageStatus, type UsageStatus } from "./proxy/usage";
import { planLabel } from "./account";

/**
 * Owner-only admin layer. Membership is an explicit allow-list of emails in the
 * ADMIN_EMAILS env var (comma-separated). The check runs against the verified
 * session email, never anything from the request. There is no self-service
 * route to this data — only listed emails see it.
 */

export function isAdmin(email: string | null | undefined): boolean {
  if (!email) return false;
  const allow = (process.env.ADMIN_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  return allow.includes(email.toLowerCase());
}

/** Rough monthly value of a tier, for an MRR estimate. */
export function priceForPlan(label: string): number {
  if (label === "Ultra") return 50;
  if (label === "Pro") return 20;
  return 0;
}

export interface AdminAccount {
  customerEmail: string | null;
  planLabel: string;
  status: LicenseStatus;
  currentPeriodEnd: string | null;
  trialEndsAt: string | null;
  createdAt: string;
  claimed: boolean;
  usage: UsageStatus;
}

/**
 * Every license in the store, newest first, each with its current usage. Reads
 * with the service role — this is the one place that intentionally crosses all
 * users, and it's reachable only after an isAdmin() gate.
 */
export async function getAllAccounts(): Promise<AdminAccount[]> {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("licenses")
    .select(
      "license_key, customer_email, status, plan, current_period_end, trial_ends_at, created_at, user_id",
    )
    .order("created_at", { ascending: false });

  if (error) {
    console.error("getAllAccounts failed:", error.message);
    return [];
  }

  const rows = data ?? [];
  return Promise.all(
    rows.map(async (row) => ({
      customerEmail: (row.customer_email as string | null) ?? null,
      planLabel: planLabel((row.plan as string | null) ?? null),
      status: row.status as LicenseStatus,
      currentPeriodEnd: (row.current_period_end as string | null) ?? null,
      trialEndsAt: (row.trial_ends_at as string | null) ?? null,
      createdAt: row.created_at as string,
      claimed: Boolean(row.user_id),
      usage: await getUsageStatus(
        row.license_key as string,
        (row.plan as string | null) ?? null,
      ),
    })),
  );
}

export interface WaitlistEntry {
  email: string;
  source: string | null;
  createdAt: string;
}

export interface WaitlistSummary {
  total: number;
  recent: WaitlistEntry[];
}

/**
 * Waitlist count and the most-recent entries (see migration_waitlist.sql). Reads
 * with the service role; reachable only behind the admin gate.
 */
export async function getWaitlist(limit = 50): Promise<WaitlistSummary> {
  const supabase = getSupabaseAdmin();
  const { count, error: countErr } = await supabase
    .from("waitlist")
    .select("*", { count: "exact", head: true });
  if (countErr) {
    console.error("getWaitlist count failed:", countErr.message);
    return { total: 0, recent: [] };
  }

  const { data, error } = await supabase
    .from("waitlist")
    .select("email, source, created_at")
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) {
    console.error("getWaitlist rows failed:", error.message);
    return { total: count ?? 0, recent: [] };
  }

  return {
    total: count ?? 0,
    recent: (data ?? []).map((r) => ({
      email: r.email as string,
      source: (r.source as string | null) ?? null,
      createdAt: r.created_at as string,
    })),
  };
}

export interface UsageInsights {
  /** Accounts that have synced a snapshot at least once. */
  syncedAccounts: number;
  /** Sum of tasks run across all synced accounts. */
  totalTasks: number;
  /** Most-used actions across all users, highest first. */
  topActions: { action: string; count: number }[];
  /** Most-opened apps across all users, highest first. `icon` is a data URI
   *  of the app's real macOS icon when we have one, else undefined. */
  topApps: { label: string; count: number; icon?: string }[];
  /** Most-opened site domains across all users, highest first. */
  topSites: { label: string; count: number }[];
  /** How many synced accounts have each integration working. */
  connections: { calendar: number; mail: number; notes: number };
}

interface SyncStats {
  total_tasks?: number;
  top_actions?: { action: string; count: number }[];
  top_apps?: { label: string; count: number }[];
  top_sites?: { label: string; count: number }[];
}
interface SyncConnections {
  calendar?: boolean;
  mail?: boolean;
  notes?: boolean;
}

/**
 * Cross-user behavioral rollup from the account_sync snapshots: what people
 * actually do most and which connections they have working. This is the one
 * aggregate the per-subscriber view doesn't give you. Service-role read behind
 * the admin gate; the snapshots themselves carry no raw prompts.
 */
export async function getUsageInsights(): Promise<UsageInsights> {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("account_sync")
    .select("stats, connections");
  if (error) {
    console.error("getUsageInsights failed:", error.message);
    return {
      syncedAccounts: 0,
      totalTasks: 0,
      topActions: [],
      topApps: [],
      topSites: [],
      connections: { calendar: 0, mail: 0, notes: 0 },
    };
  }

  const rows = data ?? [];
  const actionTotals = new Map<string, number>();
  const appTotals = new Map<string, number>();
  const siteTotals = new Map<string, number>();
  const connections = { calendar: 0, mail: 0, notes: 0 };
  let totalTasks = 0;

  // Sum a [{label,count}] list from one snapshot into a running total map.
  const addLabeled = (
    into: Map<string, number>,
    items?: { label: string; count: number }[],
  ) => {
    for (const it of items ?? []) {
      if (!it?.label) continue;
      into.set(it.label, (into.get(it.label) ?? 0) + (Number(it.count) || 0));
    }
  };

  for (const row of rows) {
    const stats = (row.stats as SyncStats | null) ?? null;
    const conns = (row.connections as SyncConnections | null) ?? null;
    if (stats) {
      totalTasks += Number(stats.total_tasks) || 0;
      for (const a of stats.top_actions ?? []) {
        if (!a?.action) continue;
        actionTotals.set(
          a.action,
          (actionTotals.get(a.action) ?? 0) + (Number(a.count) || 0),
        );
      }
      addLabeled(appTotals, stats.top_apps);
      addLabeled(siteTotals, stats.top_sites);
    }
    if (conns) {
      if (conns.calendar) connections.calendar += 1;
      if (conns.mail) connections.mail += 1;
      if (conns.notes) connections.notes += 1;
    }
  }

  const topActions = [...actionTotals.entries()]
    .map(([action, count]) => ({ action, count }))
    .sort((a, b) => b.count - a.count);
  const rankLabeled = (m: Map<string, number>) =>
    [...m.entries()]
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 12);

  const topApps: { label: string; count: number; icon?: string }[] =
    rankLabeled(appTotals);

  // Attach real app icons (shared, deduped app_icons store) to the ranked apps.
  // Best-effort: a lookup failure just leaves apps without an icon (the
  // dashboard then renders a letter badge). Keyed by lower(trim(label)) to
  // match the slug the sync route writes.
  const slugFor = (label: string) => label.trim().toLowerCase();
  const slugs = topApps.map((a) => slugFor(a.label));
  if (slugs.length) {
    const { data: iconRows, error: iconErr } = await supabase
      .from("app_icons")
      .select("slug, png_base64")
      .in("slug", slugs);
    if (iconErr) {
      console.error("getUsageInsights app_icons failed:", iconErr.message);
    } else {
      const byslug = new Map(
        (iconRows ?? []).map((r) => [
          r.slug as string,
          r.png_base64 as string,
        ]),
      );
      for (const app of topApps) {
        const png = byslug.get(slugFor(app.label));
        if (png) app.icon = `data:image/png;base64,${png}`;
      }
    }
  }

  return {
    syncedAccounts: rows.length,
    totalTasks,
    topActions,
    topApps,
    topSites: rankLabeled(siteTotals),
    connections,
  };
}
