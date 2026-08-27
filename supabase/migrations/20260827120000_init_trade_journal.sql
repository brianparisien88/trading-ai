-- Migration: initial trade journal schema
-- Source of truth: docs/supabase_schema_draft.md
-- Scope: two tables, matching the two dashboard tabs. Intentionally minimal —
-- expand only when a real need shows up, not preemptively.
--
-- Historical data migration (the 242 reconstructed round trips) is DEFERRED per
-- docs/build_brief_addendum_2026-08-27.md — this migration only creates structure.

-- ============================================================================
-- closed_trades
-- One row per FIFO-reconstructed round-trip trade (trade_pipeline.py output).
-- Inserted once by the sync job (dedup on `id`); updated only by the user's
-- own journal edits. Never otherwise mutated after insert.
-- ============================================================================
create table if not exists public.closed_trades (
  id                    text primary key,          -- {entry_order}_{exit_order}_{n} from trade_pipeline.py; stable across syncs
  symbol                text not null,             -- underlying ticker
  entry_time            timestamptz,               -- null if no matching open was found (ambiguous)
  entry_price           numeric,                   -- null if no matching open
  exit_time             timestamptz,
  exit_price            numeric,
  size                  numeric,                   -- contracts
  pnl                   numeric,                   -- realized P&L, this leg
  commission            numeric,                   -- entry + exit commission, attributed
  cost_basis            numeric,                   -- null if entry unknown
  return_pct            numeric,                   -- pnl / cost_basis * 100
  days_held             numeric,
  ambiguous             boolean not null default false,  -- FIFO pairing flagged this leg for manual verification
  ambiguous_reason      text,
  entry_order_id        bigint,                    -- IBKR order id, traceability
  exit_order_id         bigint,
  strategy              text,                      -- user-entered (free-text / enum-ish; no lookup table yet)
  journal_thoughts      text,                      -- user-entered
  planned_stop          numeric,                   -- user-entered, at logging time
  planned_target        numeric,                   -- user-entered, at logging time
  contract_description  text,                      -- strike/expiry; only for trades closed after capture-on-close is built. Historical rows stay null — do NOT backfill with guesses.
  synced_at             timestamptz not null default now()  -- last time the sync job wrote/touched this row
);

comment on table public.closed_trades is
  'One row per FIFO-reconstructed round-trip trade. id is the natural dedup key so the daily sync can run idempotently.';

create index if not exists closed_trades_exit_time_idx  on public.closed_trades (exit_time desc);
create index if not exists closed_trades_symbol_idx     on public.closed_trades (symbol);
create index if not exists closed_trades_ambiguous_idx  on public.closed_trades (ambiguous) where ambiguous;

-- ============================================================================
-- open_positions
-- One row per currently-open position. Fully truncated and rewritten by the
-- sync job each run — current state only, no history.
-- (If "what was open last Tuesday" ever becomes a real question, that's a
--  snapshot_date column + no-truncate insert — not needed yet.)
-- ============================================================================
create table if not exists public.open_positions (
  id                     text primary key,         -- open_{contract_id} from live IBKR position data
  symbol                 text not null,
  contract_description   text,                     -- full detail incl. strike/expiry — always available for OPEN positions
  entry_time             timestamptz,              -- null if unverified_entry_date
  entry_price            numeric,                  -- IBKR live average_price (includes commission) — authoritative, not reconstructed
  cost_basis             numeric,
  market_value           numeric,
  unrealized_pnl         numeric,
  daily_pnl              numeric,
  dte                    integer,                  -- days to expiry
  iv                     numeric,
  volume                 integer,
  open_interest          integer,
  unverified_entry_date  boolean not null default false,  -- true if no FIFO match (position opened outside the fetched trade-history window)
  synced_at              timestamptz not null default now()
);

comment on table public.open_positions is
  'Current open positions only. Truncated + rewritten every sync run. entry_price comes from IBKR live average_price and is authoritative.';

create index if not exists open_positions_symbol_idx on public.open_positions (symbol);

-- ============================================================================
-- Row-Level Security
-- ----------------------------------------------------------------------------
-- Single-tenant personal use. Two access paths:
--   * Dashboard (browser)  -> publishable key  sb_publishable_...  -> `anon` role -> RLS APPLIES
--   * Sync job (GitHub Actions) -> secret key  sb_secret_...       -> BYPASSES RLS
--
-- So we only need a SELECT policy for the dashboard. With no INSERT/UPDATE/DELETE
-- policy, the publishable key cannot write; the secret key still writes freely
-- because it bypasses RLS.
--
-- OPEN DECISION (dashboard rebuild, not this migration): journal edits
-- (strategy / journal_thoughts / planned_stop / planned_target) are written from
-- the browser today. Options when we get there:
--   a) add Supabase Auth + a policy scoped to those columns for `authenticated`
--   b) route journal writes through a tiny authenticated endpoint (GitHub Pages
--      has no backend, so this means a serverless fn or Supabase Edge Function)
--   c) accept a column-scoped permissive UPDATE policy for `anon` (single-tenant
--      obscurity — weakest, but matches "left fully open behind the key" option
--      in the schema draft)
-- Left unresolved here on purpose — no write policy is created.
-- ============================================================================
alter table public.closed_trades enable row level security;
alter table public.open_positions enable row level security;

create policy "public read closed_trades"
  on public.closed_trades
  for select
  to anon, authenticated
  using (true);

create policy "public read open_positions"
  on public.open_positions
  for select
  to anon, authenticated
  using (true);
