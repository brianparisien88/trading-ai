-- Move the Trade History "Strategy" dropdown list out of hardcoded JS into a
-- table so a logged-in user can add/remove entries from the dashboard.
-- Public read (the dropdown loads for everyone); authenticated CRUD.
-- Deleting an option does NOT touch any trade's saved `strategy` value —
-- strategyOptionsHTML() still shows a trade's current value even if it's no
-- longer in the list.

create table strategy_options (
  label      text primary key,
  sort_order int not null default 100,
  created_at timestamptz not null default now()
);

alter table strategy_options enable row level security;
create policy "public read strategy_options"
  on strategy_options for select using (true);
create policy "authenticated manage strategy_options"
  on strategy_options for all to authenticated using (true) with check (true);

insert into strategy_options (label, sort_order) values
  ('Reversal / counter-trend', 10),
  ('Continuation', 20),
  ('Chop / range', 30),
  ('1M double bottom / 2D double bottom - 8H/45min ready', 100),
  ('1M double bottom / 2D HH/HL - 8H/45min ready', 110),
  ('1M HH/HL / 2D double bottom - 8H/45min ready', 120),
  ('1M HH/HL / 2D HH/HL - 8H/45min ready', 130),
  ('2D double bottom - 8H/45min ready', 140),
  ('2D HH/HL - 8H/45min ready', 150),
  ('Relative Strength continuation - bull flag break', 160),
  ('Relative Strength continuation - bottom of range/support line', 170)
on conflict (label) do nothing;
