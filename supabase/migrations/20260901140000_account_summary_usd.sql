-- Migration: account_summary gains USD-normalized figures.
-- net_liquidation/available_funds stay in the account's BASE currency as
-- recorded (CAD historically; the user is switching IBKR's base currency to
-- USD, but Flex may lag a day or two, and the sync should be correct either
-- way). fx_usdcad is the USD/CAD rate the sync used (Yahoo Finance, keyless,
-- best-effort — null if the fetch failed, in which case the _usd columns are
-- left null rather than guessed). *_usd columns let the dashboard show
-- Account Balance / Capital Available in USD alongside the already-USD
-- Unrealized P&L / Capital Deployed tiles, without changing the source figures.

alter table public.account_summary
  add column if not exists fx_usdcad numeric,
  add column if not exists net_liquidation_usd numeric,
  add column if not exists available_funds_usd numeric;

comment on column public.account_summary.fx_usdcad is
  'USD/CAD rate (1 USD = N CAD) used to derive the *_usd columns this sync, from Yahoo Finance CAD=X.';
comment on column public.account_summary.net_liquidation_usd is
  'net_liquidation converted to USD (pass-through if currency is already USD).';
comment on column public.account_summary.available_funds_usd is
  'available_funds converted to USD (pass-through if currency is already USD).';
