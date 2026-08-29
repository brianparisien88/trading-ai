-- Migration: VIX level at entry / exit on closed_trades
-- Depends on: 20260829180000_closed_trades_horizon.sql
--
-- The sync fills these from Yahoo Finance's ^VIX daily closes (one keyless call).
-- Lets the dashboard show, and analysis bucket, ROI vs the market vol regime
-- at the time each contract was bought.
-- Applied 2026-08-29 via the Supabase MCP.

alter table public.closed_trades
  add column if not exists vix_at_entry numeric,
  add column if not exists vix_at_exit  numeric;
