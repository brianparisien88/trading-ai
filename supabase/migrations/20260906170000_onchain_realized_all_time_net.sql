-- Migration: all-time Realized P&L also net of gas/spread, matching the
-- since-Nov-2025 figure's convention (user's call, 2026-09-06). The
-- dashboard's "Total Fees" / "Fees since 11/25" tiles are being removed --
-- P&L should encompass costs directly rather than showing a raw number next
-- to a separate fees tile the reader has to net themselves.

alter table public.onchain_summary
  add column if not exists realized_all_time_net_usd numeric;

comment on column public.onchain_summary.realized_all_time_net_usd is
  'All-time realized P&L (realized_matched_usd) net of gas fees and swap spread. Dashboard "Total P&L" tile as of 2026-09-06 (was the raw matched figure, shown alongside a separate Total Fees tile that no longer exists).';
