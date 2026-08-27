-- Migration: account_summary — one-row table for the dashboard's account tiles
-- (Account Balance / Capital Available). Fed by the daily sync from the Flex
-- report's NAV + Cash Report sections. Values are in the account BASE currency
-- (CAD), unlike the USD position-level figures — the dashboard labels each.

create table if not exists public.account_summary (
  id               text primary key default 'current',  -- always 'current' — single row, rewritten each sync
  net_liquidation  numeric,   -- total account value (EquitySummaryByReportDateInBase.total)
  available_funds  numeric,   -- settled cash available to trade (cash account) — CashReport BASE_SUMMARY
  currency         text,      -- base currency, e.g. CAD
  as_of            date,      -- report date the figures are from
  synced_at        timestamptz not null default now()
);

comment on table public.account_summary is
  'Single row (id = current). Account-level balances in BASE currency (CAD). Rewritten every sync.';

alter table public.account_summary enable row level security;

create policy "public read account_summary"
  on public.account_summary
  for select
  to anon, authenticated
  using (true);

revoke insert, update, delete on public.account_summary from anon, authenticated;
