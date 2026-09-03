-- Migration: Strategy field becomes multi-select (array) on both journal tables.
-- Was a single text value; the dashboard now lets a trade carry more than one
-- strategy_options label at once. Existing single values are preserved as a
-- one-element array. Column-scoped GRANT UPDATE (from 20260827130000 /
-- 20260830210000) is on the column name, not its type, so it survives
-- unchanged -- no RLS/grant changes needed here.

alter table public.closed_trades
  alter column strategy type text[]
  using case when strategy is null then null else array[strategy] end;

alter table public.open_positions
  alter column strategy type text[]
  using case when strategy is null then null else array[strategy] end;

comment on column public.closed_trades.strategy is
  'Multi-select: array of strategy_options labels the user tagged this trade with. Null/empty = untagged.';
comment on column public.open_positions.strategy is
  'Multi-select: array of strategy_options labels the user tagged this trade with. Null/empty = untagged.';
