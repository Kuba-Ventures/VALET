-- [PRODUCT_NAME] license + entitlement store.
-- Run this once against your Supabase project (SQL editor or psql).

create extension if not exists "pgcrypto";

create table if not exists public.licenses (
  id                      uuid primary key default gen_random_uuid(),
  license_key             text not null unique,
  stripe_customer_id      text,
  stripe_subscription_id  text,
  status                  text not null default 'invalid'
                            check (status in ('active','trialing','past_due','canceled','invalid')),
  plan                    text,
  trial_ends_at           timestamptz,
  current_period_end      timestamptz,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz,
  last_validated_at       timestamptz
);

create index if not exists licenses_license_key_idx
  on public.licenses (license_key);

create index if not exists licenses_stripe_subscription_id_idx
  on public.licenses (stripe_subscription_id);

-- Row Level Security: the app only ever reaches this table through the service
-- role key on the server, which bypasses RLS. Enable RLS with no public policy
-- so nothing is readable with the anon/public key.
alter table public.licenses enable row level security;
