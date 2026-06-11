-- VALET desktop → account sync store.
-- Run AFTER the earlier migrations, once, against your Supabase project.
--
-- The desktop app pushes a small, privacy-preserving snapshot up to
-- /api/proxy/sync (authed by the license key): the profile the user entered in
-- onboarding, aggregate speech/usage stats (NO raw prompts), and which apps are
-- connected. One row per license — the app overwrites it on each sync.
--
-- JSONB keeps the shape flexible as the app evolves without a migration per
-- field. Same access posture as the rest: RLS on, no public policy; account
-- pages read this only through server routes scoped to the verified user.

create table if not exists public.account_sync (
  license_key   text primary key
                  references public.licenses (license_key) on delete cascade,
  profile       jsonb,
  stats         jsonb,
  connections   jsonb,
  app_version   text,
  updated_at    timestamptz not null default now()
);

alter table public.account_sync enable row level security;
