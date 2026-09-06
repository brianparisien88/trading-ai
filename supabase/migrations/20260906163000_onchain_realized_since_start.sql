-- Migration: Realized P&L headline moves to a fixed since-Nov-2025 window,
-- net of gas/spread on those trades -- replacing the all-time matched/all-in
-- split as the dashboard's top-line number (user's call, 2026-09-06). The
-- rolling 1-yr "Value Change" tile is retired in the same change; its columns
-- (pnl_window_*) are left in place but explicitly nulled by the sync going
-- forward rather than dropped.

alter table public.onchain_summary
  add column if not exists realized_since_start_usd numeric;

comment on column public.onchain_summary.realized_since_start_usd is
  'Realized P&L since WINDOW_START (2025-11-01, fixed, not rolling) net of gas fees and swap spread on those same trades. Dashboard headline "Realized P&L" as of 2026-09-06.';
