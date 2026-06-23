-- Fix: VIP/checkout signups created 2–3 duplicate license rows per subscription.
--
-- Cause: a single signup fires several Stripe events nearly simultaneously
-- (customer.subscription.created / .updated + checkout.session.completed, plus
-- the success page), and the old code did a read-then-insert. Each caller's
-- SELECT found nothing yet, so each INSERTed a fresh row (same
-- stripe_subscription_id, different license_key).
--
-- This migration (1) collapses existing duplicates to a single row per
-- subscription, then (2) adds a UNIQUE index on stripe_subscription_id so the
-- new atomic upsert (INSERT ... ON CONFLICT DO NOTHING) can never duplicate
-- again. Run this in the Supabase SQL editor BEFORE (or with) deploying the
-- code change — the upsert's ON CONFLICT needs this index to exist.
--
-- Safe: duplicate rows have no usage (license_usage references license_key with
-- ON DELETE CASCADE, so the empty usage rows for deleted dupes drop cleanly),
-- and we keep the row most likely to be the real one.

begin;

-- 1. Delete duplicate rows, keeping the best per stripe_subscription_id:
--    claimed-by-account first, then most usage, then oldest.
with ranked as (
  select
    l.id,
    row_number() over (
      partition by l.stripe_subscription_id
      order by
        (l.user_id is not null) desc,        -- a claimed account wins
        coalesce(u.requests, 0) desc,        -- otherwise the row that's been used
        l.created_at asc                      -- tie-break: the original
    ) as rn
  from public.licenses l
  left join public.license_usage u on u.license_key = l.license_key
  where l.stripe_subscription_id is not null
)
delete from public.licenses
where id in (select id from ranked where rn > 1);

-- 2. Enforce one license per subscription going forward. Partial (WHERE NOT
--    NULL) because stripe_subscription_id is nullable and multiple NULLs must
--    remain allowed. The new code upserts with onConflict on this column.
create unique index if not exists licenses_stripe_subscription_id_uniq
  on public.licenses (stripe_subscription_id)
  where stripe_subscription_id is not null;

commit;
