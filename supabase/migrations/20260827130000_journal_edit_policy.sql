-- Migration: allow a logged-in user to edit journal fields on closed_trades
-- Depends on: 20260827120000_init_trade_journal.sql
--
-- Decision (build brief follow-up): journal edits use Supabase Auth. A signed-in
-- user may update ONLY the four journal columns on closed_trades. Everything else
-- stays read-only from the browser; the sync job (secret key) is unaffected
-- because it bypasses RLS and has its own grants.
--
-- How the restriction works — two independent gates, both required:
--   1. Column GRANT  -> which columns `authenticated` may write at all
--   2. RLS policy     -> which rows `authenticated` may target
-- Postgres enforces both. Column-level control is the GRANT, not the policy
-- (RLS is row-level only).

-- 1. Remove the blanket table privileges Supabase grants by default, so the
--    only write path left for anon/authenticated is the narrow one below.
revoke insert, update, delete on public.closed_trades from anon, authenticated;
revoke insert, update, delete on public.open_positions from anon, authenticated;

-- 2. Re-grant UPDATE on just the journal columns, to signed-in users only.
grant update (strategy, journal_thoughts, planned_stop, planned_target)
  on public.closed_trades
  to authenticated;

-- 3. RLS policy so the update is allowed through at the row level.
--    Single-tenant: any row, any value (column set is already fenced by the grant).
create policy "authenticated can edit journal fields"
  on public.closed_trades
  for update
  to authenticated
  using (true)
  with check (true);

comment on policy "authenticated can edit journal fields" on public.closed_trades is
  'Journal edits from the dashboard. Column set is restricted by the GRANT UPDATE (strategy, journal_thoughts, planned_stop, planned_target) — this policy only opens the rows.';
