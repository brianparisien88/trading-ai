-- Migration: contract-aware "if I had held" horizon on closed_trades
-- Depends on: 20260829120000_closed_trades_context.sql
--
-- The Since Exit / Verdict metric now compares the underlying at exit against
-- its price at the CONTRACT'S OWN EXPIRY (or today, if it hasn't expired) --
-- not just "today" regardless of how long ago the trade closed.

alter table public.closed_trades
  add column if not exists underlying_price_horizon  numeric,   -- stock close at min(contract_expiry, today)
  add column if not exists contract_horizon_date     date,      -- that date
  add column if not exists underlying_price_peak      numeric,   -- most favourable close between exit and horizon
  add column if not exists underlying_peak_date       date;
