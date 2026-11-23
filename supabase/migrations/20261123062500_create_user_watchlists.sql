-- Migration: create_user_watchlists_with_rls_and_guards
-- Target Supabase project: Alfa Terminal (utgmsaozryszatkltzym)
-- Applied directly via Supabase MCP execute_sql on 2026-11-23.
-- This file records the exact idempotent DDL, RLS policies, CHECK constraints,
-- triggers, and RPC privilege revocations currently live on public.user_watchlists.

create table if not exists public.user_watchlists (
    user_id uuid not null references auth.users(id) on delete cascade,
    symbol text not null,
    name text not null,
    sector text not null,
    stage text not null,
    timeframe text not null,
    current_price numeric not null,
    trigger_price numeric not null,
    support_price numeric not null,
    invalidation_price numeric not null,
    target_1 numeric not null,
    target_2 numeric not null,
    distance_to_trigger_pct numeric not null,
    score numeric not null,
    thesis text not null,
    checklist_passed integer not null default 5,
    checklist_total integer not null default 5,
    priority text not null default 'HIGH',
    notes text not null default '',
    added_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, symbol),
    constraint chk_user_watchlists_symbol_len check (char_length(symbol) between 2 and 24),
    constraint chk_user_watchlists_name_len check (char_length(name) <= 120),
    constraint chk_user_watchlists_sector_len check (char_length(sector) <= 120),
    constraint chk_user_watchlists_stage_len check (char_length(stage) <= 120),
    constraint chk_user_watchlists_timeframe_len check (char_length(timeframe) <= 60),
    constraint chk_user_watchlists_thesis_len check (char_length(thesis) <= 2000),
    constraint chk_user_watchlists_notes_len check (char_length(notes) <= 2000),
    constraint chk_user_watchlists_prices_positive check (
        current_price >= 0 and
        trigger_price > 0 and
        support_price > 0 and
        invalidation_price > 0 and
        target_1 > 0 and
        target_2 > 0
    )
);

create index if not exists idx_user_watchlists_user_score
    on public.user_watchlists (user_id, score desc, added_at desc);

alter table public.user_watchlists enable row level security;
alter table public.user_watchlists force row level security;

drop policy if exists "Users can view their own watchlist" on public.user_watchlists;
create policy "Users can view their own watchlist"
    on public.user_watchlists
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists "Users can insert into their own watchlist" on public.user_watchlists;
create policy "Users can insert into their own watchlist"
    on public.user_watchlists
    for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists "Users can update their own watchlist" on public.user_watchlists;
create policy "Users can update their own watchlist"
    on public.user_watchlists
    for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

drop policy if exists "Users can delete from their own watchlist" on public.user_watchlists;
create policy "Users can delete from their own watchlist"
    on public.user_watchlists
    for delete
    to authenticated
    using ((select auth.uid()) = user_id);

grant select, insert, update, delete on table public.user_watchlists to authenticated;
revoke all on table public.user_watchlists from anon;

-- Per-user row quota (max 50 symbols per account)
create or replace function public.enforce_user_watchlist_quota()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  current_count integer;
begin
  select count(*) into current_count
  from public.user_watchlists
  where user_id = NEW.user_id;

  if current_count >= 50 then
    raise exception 'Watchlist quota exceeded: maximum 50 symbols allowed per user account.'
      using errcode = 'P0001';
  end if;
  return NEW;
end;
$$;

drop trigger if exists trg_enforce_user_watchlist_quota on public.user_watchlists;
create trigger trg_enforce_user_watchlist_quota
before insert on public.user_watchlists
for each row
execute function public.enforce_user_watchlist_quota();

-- Canonical uppercase alphanumeric symbol normalization & server-enforced timestamps
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
  if TG_OP = 'INSERT' then
    NEW.added_at := now();
  else
    NEW.added_at := OLD.added_at;
  end if;
  NEW.updated_at := now();
  return NEW;
end;
$$;

drop trigger if exists trg_normalize_user_watchlist_row on public.user_watchlists;
create trigger trg_normalize_user_watchlist_row
before insert or update on public.user_watchlists
for each row
execute function public.normalize_user_watchlist_row();

-- Remediate Supabase advisor lints 0028/0029 (revoke public/anon/authenticated RPC execute)
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;

