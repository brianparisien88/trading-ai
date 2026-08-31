-- Open Trades tab parity with Trade History: contract detail, underlying stock
-- price at entry / latest, the price window for the inline chart, and the
-- intent-aware Setup Score. All sync-owned (computed from Flex + Yahoo, no LLM);
-- the journal columns are untouched.

alter table open_positions
  add column if not exists contract_type            text,
  add column if not exists contract_strike          numeric,
  add column if not exists contract_expiry          date,
  add column if not exists underlying_price_entry    numeric,
  add column if not exists underlying_price_latest   numeric,
  add column if not exists underlying_price_latest_at timestamptz,
  add column if not exists price_window              jsonb,
  add column if not exists setup_structure           text,
  add column if not exists setup_score               integer,
  add column if not exists setup_reasons             text,
  add column if not exists setup_criteria            jsonb,
  add column if not exists setup_entry               text,
  add column if not exists chart_read_entry          text;
