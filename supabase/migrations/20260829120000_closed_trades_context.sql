-- Migration: extra context columns on closed_trades
-- Depends on: 20260827120000_init_trade_journal.sql
--
-- Populated by the sync job (secret key, bypasses RLS). None of these are
-- user-editable — the column GRANT for `authenticated` still only covers the
-- 4 journal columns, so the dashboard can't write them.

alter table public.closed_trades
  add column if not exists contract_expiry             date,       -- option expiry
  add column if not exists contract_type               text,       -- 'C' or 'P'
  add column if not exists contract_strike             numeric,
  add column if not exists underlying_price_entry      numeric,    -- stock close on the entry date
  add column if not exists underlying_price_exit       numeric,    -- stock close on the exit date
  add column if not exists underlying_price_latest     numeric,    -- stock price at the last sync
  add column if not exists underlying_price_latest_at  timestamptz,
  add column if not exists trade_seq                   integer;    -- Nth closed round-trip, chronological

-- contract_description already exists; the sync now fills it from Flex
-- (real strike/expiry off the execution records — not a guess), so the
-- "don't backfill" note in supabase_schema_draft.md no longer applies.
comment on column public.closed_trades.contract_description is
  'e.g. "20NOV26 19 C". Filled by the sync from the Flex execution records.';
