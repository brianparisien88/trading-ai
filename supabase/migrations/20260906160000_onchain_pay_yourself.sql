-- Migration: "Pay Yourself" tracking on onchain_summary.
-- Stablecoin sent from the tracked MetaMask wallet to a confirmed real-world
-- cash-out destination (Lulubit, Crypto.com), NOT a loss against trading
-- P&L -- profit taken off-chain to spend. Fixed start date (2025-11-01), not
-- a rolling window, so this keeps accumulating rather than resetting.
-- Destination addresses live in sync/onchain.py's PAY_YOURSELF_ADDRESSES,
-- not in the DB -- update that constant (with the user's confirmation) to
-- add more.

alter table public.onchain_summary
  add column if not exists pay_yourself_usd numeric,
  add column if not exists pay_yourself_since date;

comment on column public.onchain_summary.pay_yourself_usd is
  'Stablecoin sent to a confirmed cash-out address (see PAY_YOURSELF_ADDRESSES in sync/onchain.py) since pay_yourself_since. Not counted against realized P&L.';
comment on column public.onchain_summary.pay_yourself_since is
  'Fixed start date for the pay_yourself_usd tally (2025-11-01) -- accumulates forward, not a rolling window.';
