-- Fix: Stripe webhook 500s on every subscription/checkout event, causing Stripe
-- to disable the endpoint after repeated failures.
--
-- Cause: migration_dedupe_licenses.sql created a PARTIAL unique index on
-- stripe_subscription_id (WHERE stripe_subscription_id IS NOT NULL) intending it
-- to back the webhook's upsert(onConflict: "stripe_subscription_id"). But
-- Postgres cannot match a bare-column `ON CONFLICT (stripe_subscription_id)`
-- (which supabase-js emits) to a PARTIAL index — it raises 42P10, "there is no
-- unique or exclusion constraint matching the ON CONFLICT specification". The
-- upsert error was unchecked in lib/license.ts, so nothing inserted and the
-- read-back threw "no row found ... after upsert" -> HTTP 500.
--
-- Fix: add a FULL unique constraint on stripe_subscription_id so ON CONFLICT
-- arbitration works. Safe: the partial index already guarantees no duplicate
-- non-null values exist, and a standard UNIQUE constraint permits multiple NULLs
-- (Postgres treats NULLs as distinct), so nullable rows are unaffected.
--
-- The partial index is now redundant (the full constraint subsumes it for the
-- non-null case) but is left in place to avoid churn; it can be dropped later.
--
-- Applied to production (project ufqvgujnphaejewqmugg) on 2026-07-05.

alter table public.licenses
  add constraint licenses_stripe_subscription_id_key unique (stripe_subscription_id);
