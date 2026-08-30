-- Migration: per-trade price window + templated trend reads on closed_trades
-- Depends on: 20260829200000_closed_trades_vix.sql
-- Applied 2026-08-30 via the Supabase MCP.
--
-- price_window     [[date, close], ...] ~45 trading days each side of the trade,
--                  for the mini price chart in the expand panel.
-- setup_entry      rule-based label at entry: momentum breakout / early reversal /
--                  falling knife / range / extended.
-- chart_read_*     deterministic templated prose assembled from the trend metrics
--                  (no LLM). Re-derived every sync.

alter table public.closed_trades
  add column if not exists price_window     jsonb,
  add column if not exists setup_entry      text,
  add column if not exists chart_read_entry text,
  add column if not exists chart_read_exit  text;
