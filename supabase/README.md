# Supabase — Trade Journal

Schema for the trade journal. Two tables, per [`docs/supabase_schema_draft.md`](../docs/supabase_schema_draft.md):

- **`closed_trades`** — one row per FIFO-reconstructed round-trip trade
- **`open_positions`** — current open positions, truncated + rewritten each sync

## Migrations

| File | What it does |
|---|---|
| `migrations/20260827120000_init_trade_journal.sql` | Creates both tables, indexes, enables RLS, adds public **read** policies. Applied 2026-08-27. |
| `migrations/20260827130000_journal_edit_policy.sql` | Locks down blanket write grants; lets a signed-in user (Supabase Auth) update only the 4 journal columns on `closed_trades`. Applied 2026-08-27. |
| `migrations/20260828000000_account_summary.sql` | Single-row `account_summary` table (base-currency balances) + public read. Applied 2026-08-27. |
| `migrations/20260829120000_closed_trades_context.sql` | Adds contract detail + underlying-price + `trade_seq` columns to `closed_trades`. |
| `migrations/20260829180000_closed_trades_horizon.sql` | Adds `underlying_price_horizon` / `contract_horizon_date` / `underlying_price_peak` / `underlying_peak_date` — the contract-expiry-aware "If Held" comparison. |
| `migrations/20260829200000_closed_trades_vix.sql` | Adds `vix_at_entry` / `vix_at_exit`. |
| `migrations/20260830120000_closed_trades_chart_reads.sql` | Adds `price_window` / `setup_entry` / `chart_read_entry` / `chart_read_exit` — the mini price chart + templated trend reads. |
| `migrations/20260830180000_closed_trades_setup_score_grade.sql` | Adds `setup_score`/`setup_structure`/`setup_reasons`/`setup_criteria` (static technical) + `trade_grade`/`grade_points`/`grade_reasons` (post-mortem). |

## Edge functions

| `functions/analyze-setup/` | Live Setup Score for a prospective trade. `POST {ticker, side}` → fetches Yahoo (server-side), returns score + pass/fail criteria + contract band. `verify_jwt: false`, no DB, no secrets. Called by the dashboard's Analyze tab. |

## Applying the initial migration

You need the Supabase **project ref** (the `<ref>` in `https://<ref>.supabase.co`). Pick one path:

### Option A — SQL Editor (no tooling, fastest)

1. Open the project's **SQL Editor** in the Supabase dashboard.
2. Paste the entire contents of `migrations/20260827120000_init_trade_journal.sql`.
3. Run. It's idempotent (`create table if not exists`, `create index if not exists`).
   - Note: `create policy` is **not** `if not exists`. If you re-run, drop the two
     policies first or you'll get "policy already exists".

### Option B — Supabase CLI (repeatable, preferred once set up)

```bash
brew install supabase/tap/supabase          # or: npm i -g supabase
supabase login
supabase link --project-ref wcbokczlllatengdrdes
supabase db push                            # applies everything in migrations/
```

## Verifying it worked

In the SQL Editor:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;
-- expect: closed_trades, open_positions

select tablename, policyname, cmd
from pg_policies
where schemaname = 'public';
-- expect: "public read closed_trades" / SELECT, "public read open_positions" / SELECT

select relname, relrowsecurity
from pg_class
where relname in ('closed_trades','open_positions');
-- relrowsecurity should be true for both
```

## Keys (new Supabase key system)

This project uses `sb_publishable_...` / `sb_secret_...`, **not** the old
`anon` / `service_role` names. Same security model:

| Key | Postgres role | RLS | Used by |
|---|---|---|---|
| `sb_publishable_...` | `anon` | **applies** | Dashboard (browser), read-only |
| `sb_secret_...` | — | **bypassed** | Sync job (GitHub Actions), read + write |

Because the secret key bypasses RLS, the sync job can insert/update/truncate
without any write policy. The migration therefore only defines SELECT policies.

Copy `.env.example` (repo root) to `.env` and fill in real values. `.env` is
gitignored; `.env.example` is committed.

## Journal write path — Supabase Auth

Journal columns (`strategy`, `journal_thoughts`, `planned_stop`, `planned_target`)
are editable only by a signed-in user. Enforced by
`20260827130000_journal_edit_policy.sql`: a column-scoped `GRANT UPDATE` to
`authenticated` + an RLS `UPDATE` policy. Everything else is browser-read-only.

**One-time auth setup (Supabase dashboard):**

1. **Authentication → Users → Add user → Create new user.** Enter your email +
   a password, tick **Auto Confirm User**. This is the single account the
   dashboard logs in as.
2. **Authentication → Sign In / Providers → Email:** turn **off** "Allow new
   users to sign up" (single-user tool — no public registration).
3. Leave email confirmation as-is; the auto-confirmed user above doesn't need it.

The dashboard logs in with `supabase.auth.signInWithPassword(...)` and then
`update()`s the journal columns. Publishable key stays in the client (safe).
