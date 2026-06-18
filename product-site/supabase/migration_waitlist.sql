-- VALET waitlist store.
-- Run AFTER the earlier migrations, once, against your Supabase project.
--
-- The public site captures early-access signups at /waitlist, which POST to
-- /api/proxy-less /api/waitlist (server route, service-role insert). One row per
-- email; a case-insensitive unique index makes re-submits idempotent. The admin
-- dashboard reads the count and most-recent entries.
--
-- Same access posture as the rest: RLS on, no public policy. The anon/public key
-- can neither read nor write; only the server's service-role client touches it.

create table if not exists public.waitlist (
  id          uuid primary key default gen_random_uuid(),
  email       text not null,
  source      text,
  created_at  timestamptz not null default now()
);

-- One signup per address; lets the capture route upsert-ignore duplicates.
create unique index if not exists waitlist_email_lower_idx
  on public.waitlist (lower(email));

create index if not exists waitlist_created_at_idx
  on public.waitlist (created_at desc);

alter table public.waitlist enable row level security;
