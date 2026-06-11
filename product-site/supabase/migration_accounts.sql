-- VALET self-service accounts.
-- Run AFTER migration.sql and migration_usage.sql, once, against your Supabase
-- project (SQL editor or psql).
--
-- This wires the existing license rows to Supabase Auth users so a buyer can
-- create an email+password account on valet-voice.com and see their own
-- license key, plan, billing period and fair-use usage in one place.
--
-- Two new columns on licenses:
--   customer_email — the buyer email captured at checkout. Lets a new account
--                    auto-claim any license bought with the same address.
--   user_id        — the Supabase Auth user that owns this license. NULL until
--                    claimed (by email match or by entering the key).
--
-- Access posture is unchanged: RLS stays enabled with no public policy, so the
-- anon key still reads nothing. Account pages reach these rows only through
-- server routes that verify the signed-in session and scope every query to the
-- session user's id with the service role key.

alter table public.licenses
  add column if not exists customer_email text;

alter table public.licenses
  add column if not exists user_id uuid references auth.users (id) on delete set null;

-- Case-insensitive lookup for email auto-claim.
create index if not exists licenses_customer_email_idx
  on public.licenses (lower(customer_email));

create index if not exists licenses_user_id_idx
  on public.licenses (user_id);
