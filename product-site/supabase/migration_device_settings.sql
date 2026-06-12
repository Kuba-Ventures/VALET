-- VALET web-controlled device settings (bidirectional settings, Phase 1).
-- Run AFTER the earlier migrations, once, against your Supabase project.
--
-- This is the REVERSE direction of account_sync: the web account WRITES these,
-- and the desktop app READS + APPLIES them (voice, voice id, telemetry, …), so
-- a user can manage their VALET from the browser. One row per license.
--
-- Phase 1 = storage + read/write endpoints only. The app does not consume these
-- yet (that's Phase 3). Same access posture: RLS on, no public policy; the app
-- reads via the license-key proxy route, the web reads/writes via session-scoped
-- server routes.

create table if not exists public.device_settings (
  license_key  text primary key
                 references public.licenses (license_key) on delete cascade,
  settings     jsonb       not null default '{}'::jsonb,
  updated_at   timestamptz not null default now()
);

alter table public.device_settings enable row level security;
