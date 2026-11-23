-- Migration: harden_user_watchlists_quota_and_bounds (W8 remediation)
-- Target Supabase project: Alfa Terminal (utgmsaozryszatkltzym)
-- Applied live via Supabase MCP execute_sql on 2026-11-23.
-- Fixes:
-- 1. Normalizes symbol BEFORE checking quota and allows ON CONFLICT upserts of existing
--    (user_id, symbol) rows even when the user is already at the 50-symbol limit.
-- 2. Serializes concurrent inserts per user via pg_advisory_xact_lock(hashtext(NEW.user_id::text))
--    so two concurrent inserts at 49 rows cannot race past the 50-symbol cap.
-- 3. Enforces finite, ranged numeric bounds and coherent checklist counts at the database boundary.

alter table public.user_watchlists
  add constraint chk_user_watchlists_numeric_bounds check (
    current_price <= 1e12 and
    trigger_price <= 1e12 and
    support_price <= 1e12 and
    invalidation_price <= 1e12 and
    target_1 <= 1e12 and
    target_2 <= 1e12 and
    distance_to_trigger_pct >= 0 and distance_to_trigger_pct <= 1e6 and
    score >= 0 and score <= 100
  ),
  add constraint chk_user_watchlists_checklist_bounds check (
    checklist_total between 1 and 10 and
    checklist_passed between 0 and checklist_total
  );

create or replace function public.enforce_user_watchlist_quota()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  norm_symbol text;
  existing_row_exists boolean;
  current_count integer;
begin
  norm_symbol := upper(btrim(NEW.symbol));
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(NEW.user_id::text));

  select exists(
    select 1 from public.user_watchlists
    where user_id = NEW.user_id and symbol = norm_symbol
  ) into existing_row_exists;

  if not existing_row_exists then
    select count(*) into current_count
    from public.user_watchlists
    where user_id = NEW.user_id;

    if current_count >= 50 then
      raise exception 'Watchlist quota exceeded: maximum 50 symbols allowed per user account.'
        using errcode = 'P0001';
    end if;
  end if;
  return NEW;
end;
$$;

create or replace function public.normalize_user_watchlist_row()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  NEW.symbol := upper(btrim(NEW.symbol));
  if NEW.symbol !~ '^[A-Z0-9_]{2,24}$' then
    raise exception 'Invalid symbol format: must be 2-24 uppercase alphanumeric characters.'
      using errcode = '22023';
  end if;
  if NEW.current_price = 'NaN'::numeric or NEW.trigger_price = 'NaN'::numeric
     or NEW.support_price = 'NaN'::numeric or NEW.invalidation_price = 'NaN'::numeric
     or NEW.target_1 = 'NaN'::numeric or NEW.target_2 = 'NaN'::numeric
     or NEW.distance_to_trigger_pct = 'NaN'::numeric or NEW.score = 'NaN'::numeric then
    raise exception 'Numeric watchlist fields must be finite numbers.'
      using errcode = '22003';
  end if;
  if TG_OP = 'INSERT' then
    NEW.added_at := now();
  else
    NEW.added_at := OLD.added_at;
  end if;
  NEW.updated_at := now();
  return NEW;
end;
$$;
