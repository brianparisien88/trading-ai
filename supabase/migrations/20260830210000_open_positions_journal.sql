-- Migration: journal open positions before they close
-- Depends on: 20260828000000_account_summary.sql
-- Applied 2026-08-30 via the Supabase MCP.
--
-- open_positions gains the same 4 journal columns as closed_trades plus
-- entry_order_id (from the Flex OpenPosition originatingOrderID). The sync
-- carries the notes onto the matching closed_trades row when the position
-- closes (match on entry_order_id).

alter table public.open_positions
  add column if not exists entry_order_id   bigint,
  add column if not exists strategy         text,
  add column if not exists journal_thoughts text,
  add column if not exists planned_stop     numeric,
  add column if not exists planned_target   numeric;

revoke insert, update, delete on public.open_positions from anon, authenticated;
grant update (strategy, journal_thoughts, planned_stop, planned_target)
  on public.open_positions to authenticated;

drop policy if exists "authenticated can edit open-position journal" on public.open_positions;
create policy "authenticated can edit open-position journal"
  on public.open_positions for update to authenticated
  using (true) with check (true);
