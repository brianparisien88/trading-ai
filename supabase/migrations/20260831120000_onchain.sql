-- Onchain tab: crypto holdings, matched round-trip trades, and a wallet-level
-- summary for wallet 0x3414ec2d1c63008e1cda0e2155b7334c446a0025.
-- Data comes from the Zerion API via sync/onchain.py (daily). Everything is
-- sync-owned; public read only, like the rest of the app.

create table if not exists onchain_holdings (
  id                  text primary key,          -- {chain}:{token_address}
  wallet              text not null,
  chain               text,
  symbol              text,
  name                text,
  token_address       text,
  quantity            numeric,
  price               numeric,                   -- current USD price
  value_usd           numeric,                   -- current position value
  cost_basis_usd      numeric,                   -- from Zerion / matched lots
  avg_entry_price     numeric,
  unrealized_pnl_usd  numeric,
  unrealized_pnl_pct  numeric,
  first_bought_at     timestamptz,
  is_stablecoin       boolean default false,
  is_spam             boolean default false,
  synced_at           timestamptz
);

create table if not exists onchain_trades (
  id                text primary key,            -- deterministic hash of the matched legs
  wallet            text not null,
  chain             text,
  symbol            text,
  name              text,
  token_address     text,
  entry_time        timestamptz,
  exit_time         timestamptz,
  entry_price       numeric,                     -- USD per token at entry
  exit_price        numeric,
  qty               numeric,                     -- token units closed by this round-trip
  cost_usd          numeric,                     -- USD put in
  proceeds_usd      numeric,                     -- USD taken out
  realized_pnl_usd  numeric,
  return_pct        numeric,
  hold_days         numeric,
  entry_tx          text,
  exit_tx           text,
  entry_kind        text,                        -- 'stable->token' | 'token->token' | 'native->token'
  exit_kind         text,                        -- 'token->stable' | 'token->token' | 'token->native'
  partial           boolean default false,       -- part of a partial close / rotation
  synced_at         timestamptz
);

create table if not exists onchain_summary (
  id                     text primary key default 'current',
  wallet                 text,
  portfolio_value_usd    numeric,
  value_in_coins_usd     numeric,                -- volatile crypto (non-stablecoin)
  value_in_stables_usd   numeric,                -- USD-pegged stablecoins = dry powder
  pnl_window_days        integer,                -- rolling window the pnl figure covers
  pnl_window_start       date,
  pnl_window_end         date,
  pnl_window_usd         numeric,                -- change in portfolio value over that window
  realized_pnl_all_usd   numeric,
  unrealized_pnl_usd     numeric,
  trade_count            integer,                -- matched round-trips
  open_position_count    integer,
  unmatched_activity     integer,               -- swaps we couldn't classify into a trade
  synced_at              timestamptz
);

alter table onchain_holdings enable row level security;
alter table onchain_trades   enable row level security;
alter table onchain_summary  enable row level security;

create policy "onchain_holdings public read" on onchain_holdings for select using (true);
create policy "onchain_trades public read"   on onchain_trades   for select using (true);
create policy "onchain_summary public read"  on onchain_summary  for select using (true);
