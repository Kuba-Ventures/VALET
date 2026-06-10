-- VALET proxy usage + fair-use metering store.
-- Run AFTER migration.sql, once, against your Supabase project (SQL editor or psql).
--
-- Every AI/TTS call that flows through the proxy records usage here, keyed by
-- license. The proxy reads this to compute remaining fair-use allowance and to
-- decide the over-allowance state. Counters reset on a rolling monthly window
-- (default 30 days), reset lazily on the first write after the window lapses.

create table if not exists public.license_usage (
  license_key         text primary key
                        references public.licenses (license_key) on delete cascade,
  period_start        timestamptz not null default now(),
  requests            bigint      not null default 0,
  input_tokens        bigint      not null default 0,
  output_tokens       bigint      not null default 0,
  estimated_cost_usd  numeric(12,6) not null default 0,
  last_reset          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- Same posture as licenses: reached only through the service role key, which
-- bypasses RLS. Enable RLS with no public policy so nothing is readable with the
-- anon/public key.
alter table public.license_usage enable row level security;

-- Atomic increment with lazy monthly reset. Doing the read-modify-write inside a
-- single statement avoids races between concurrent proxy requests for the same
-- license. When the current period is older than p_period_days, the counters are
-- replaced (reset) rather than added to.
create or replace function public.record_usage(
  p_license_key text,
  p_requests    bigint,
  p_input       bigint,
  p_output      bigint,
  p_cost        numeric,
  p_period_days int default 30
) returns void
language plpgsql
as $$
declare
  v_expired boolean;
begin
  insert into public.license_usage (
    license_key, requests, input_tokens, output_tokens, estimated_cost_usd
  )
  values (p_license_key, p_requests, p_input, p_output, p_cost)
  on conflict (license_key) do update set
    requests = case
      when public.license_usage.period_start < now() - make_interval(days => p_period_days)
      then p_requests else public.license_usage.requests + p_requests end,
    input_tokens = case
      when public.license_usage.period_start < now() - make_interval(days => p_period_days)
      then p_input else public.license_usage.input_tokens + p_input end,
    output_tokens = case
      when public.license_usage.period_start < now() - make_interval(days => p_period_days)
      then p_output else public.license_usage.output_tokens + p_output end,
    estimated_cost_usd = case
      when public.license_usage.period_start < now() - make_interval(days => p_period_days)
      then p_cost else public.license_usage.estimated_cost_usd + p_cost end,
    period_start = case
      when public.license_usage.period_start < now() - make_interval(days => p_period_days)
      then now() else public.license_usage.period_start end,
    last_reset = case
      when public.license_usage.period_start < now() - make_interval(days => p_period_days)
      then now() else public.license_usage.last_reset end,
    updated_at = now();
end;
$$;
