-- VALET shared app-icon store.
-- Run AFTER migration_sync.sql, once, against your Supabase project.
--
-- The desktop app captures each opened app's real macOS icon (a small 64px
-- PNG) and sends it alongside the app label in the /api/proxy/sync snapshot.
-- An app's icon is identical for everyone who has that app installed — it is
-- NOT user content — so it lives in one shared, deduplicated table keyed by a
-- normalised slug rather than per-license. The admin "Top apps" panel joins
-- top_apps labels to this table to render the true logo (falling back to a
-- letter badge for apps we have no icon for).
--
-- First writer wins: the sync route upserts with ignoreDuplicates so an
-- established icon is not rewritten on every 15-minute sync.
--
-- Same access posture as the rest: RLS on, no public policy; only the
-- service-role admin routes read/write this.

create table if not exists public.app_icons (
  slug        text primary key,           -- lower(trim(label))
  label       text not null,              -- display label as last seen
  png_base64  text not null,              -- base64 of a small PNG icon
  updated_at  timestamptz not null default now()
);

alter table public.app_icons enable row level security;
