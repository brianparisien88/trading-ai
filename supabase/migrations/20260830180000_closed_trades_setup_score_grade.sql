-- Migration: Setup Score (static, technical, entry-only) + Trade Grade (post-mortem)
-- Depends on: 20260830120000_closed_trades_chart_reads.sql
-- Applied 2026-08-30 via the Supabase MCP.
--
-- setup_score / setup_reasons / setup_criteria / setup_structure
--   Outcome-INDEPENDENT technical read of the entry. Demo v1: pivot structure
--   (HH/HL), not-extended, range position. Does NOT use ticker history.
-- trade_grade / grade_points / grade_reasons
--   Post-mortem A/B/C/D: contract fit + hold discipline + outcome.

alter table public.closed_trades
  add column if not exists setup_structure text,
  add column if not exists setup_score     integer,
  add column if not exists setup_reasons   text,
  add column if not exists setup_criteria  jsonb,
  add column if not exists trade_grade     text,
  add column if not exists grade_points    integer,
  add column if not exists grade_reasons   text;
