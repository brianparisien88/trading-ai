# CLAUDE.md — Trade Journal

IBKR options trades → reconstructed round-trip ledger → Supabase → static dashboard.
Personal, single-user. Full decision log: `docs/living-state-global-memory` (changelog
entries) + `docs/session_changelog_2026-08-26.md` + `docs/build_brief_*`.

## Architecture (deterministic ETL — no LLM at runtime)

1. **Sync** (`sync/`, GitHub Actions daily 12:00 UTC ≈ 08:00 ET): pull IBKR Flex Web
   Service XML → `trade_pipeline.py` (merge fills by order → FIFO-pair → derive) →
   enrich (contract detail from Flex; underlying stock prices from Yahoo Finance's
   keyless chart API, one call/ticker) → write Supabase with the secret key.
2. **Database** (Supabase Postgres, project ref `wcbokczlllatengdrdes`, region us-west-2).
3. **Dashboard** (`app/index.html`, GitHub Pages): reads Supabase live via supabase-js +
   publishable key; Supabase Auth (email/password) gates journal editing. 3 tabs —
   Open Trades, Trade History, **Analyze a Trade** (calls the `analyze-setup` edge
   function for a live Setup Score on a prospective trade).
4. **Edge functions** (`supabase/functions/`): `analyze-setup` — Yahoo proxy +
   Setup Score, verify_jwt off, no DB access, no secrets.

Claude's role is judgment work only (ambiguous-pair review, trend analysis), never the
daily ETL. The IBKR MCP connector is **not** used by the sync.

## Repo layout

| Path | |
|---|---|
| `app/index.html` | The dashboard. Only this folder deploys to Pages (`.github/workflows/pages.yml`). |
| `sync/sync.py` + `sync/trade_pipeline.py` | The ETL job + reconstruction logic. |
| `supabase/migrations/` | Applied by hand in the Supabase SQL Editor (no CLI/MCP auth in this env). |
| `docs/` | Briefs, strategy spec, changelog — reference only, **never** web-served. |

## Supabase

- **Keys use the new system**: `sb_publishable_...` (= `anon` role, RLS applies, safe in
  the client HTML) and `sb_secret_...` (= service_role, bypasses RLS, server-only).
  Do **not** use `anon`/`service_role` naming or pre-2026 client patterns.
- Tables: `closed_trades` (PK `id` = `{entry_order}_{exit_order}_{n}`, stable),
  `open_positions` (PK `open_{conid}`), `account_summary` (single row, id `current`).
- RLS on all: public `SELECT`; no client writes except the 4 journal columns on
  `closed_trades` for signed-in users (column GRANT + UPDATE policy).

## IBKR Flex Web Service

- Activity Flex Query **"TradeJournalSync", ID `1618686`**. Sections: Trades (Execution),
  Open Positions (Summary), NAV in Base (exclude prior report date), Cash Report
  (Base Currency Summary). Format XML, Period "Last 365 Calendar Days".
- Endpoint `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/`:
  `SendRequest` → reference code → `GetStatement`. Token lasts ~1 year
  (expires ~2027-01-26; sync fails with `code 1012` on expiry).

## Secrets / env

Local: `.env` (gitignored) — `SUPABASE_URL`, `SUPABASE_PROJECT_REF`,
`SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`. Template: `.env.example`.

GitHub Actions (repo Settings → Secrets and variables → Actions):
- Secrets: `FLEX_TOKEN`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`
- Variable: `FLEX_QUERY_ID` = `1618686`

The publishable key is intentionally hardcoded in `app/index.html`. The secret key
must never appear in `app/` or any committed file.

## Known behaviour — Flex data lag

IBKR's Flex Web Service reports **open positions and NAV as of the prior *finalised*
business day**, not live. IBKR finalises a day's statement in an overnight batch
(ready ~05:00–08:00 ET next day), so the sync runs at 12:00 UTC to catch it. A
position closed today lands in `closed_trades` and drops off `open_positions` on the
*next* day's sync — a consistent ~1-day lag. (Running just after the market close
instead would get a statement still ending the previous day, making it a 2-day lag.)
This is inherent to Flex — the headless/tokenised tradeoff; the live Client Portal
API was deliberately not used. The dashboard labels the "as of" date on the Open tab.

## Hard rules

- **Never overwrite the journal columns** (`strategy`, `journal_thoughts`,
  `planned_stop`, `planned_target`) — the sync payload deliberately omits them.
- **Historical 242-trade backfill is DEFERRED.** Don't attempt it, and never source
  fills from chat transcripts — only a real IBKR export or the sync's own window.
- `closed_trades` contract detail, underlying prices, `vix_at_*`, `price_window`,
  `setup_entry`, and the `chart_read_*` templated blurbs are all **sync-owned** —
  computed from Flex + Yahoo (no LLM), not user-editable, recomputed every run.
- `price_window` (jsonb, bulky) is excluded from the dashboard's bulk load and
  lazy-fetched per row on expand.
- Ambiguous FIFO pairings (`ambiguous = true`) are shown, never silently trusted.
- Account-level figures (`account_summary`) are CAD (base currency); position-level
  figures are USD. Always label currency; never combine them unlabeled.
- **Never let an empty/failed upstream fetch mutate the DB.** Sync jobs `die`/abort
  on a zero-row fetch that normally has rows; stale-row cleanup runs only on
  tables written that run; transient failures (rate limit / 5xx / empty) raise
  `TransientError` → exit 0, tables untouched. See `docs/BUILD-PLAYBOOK.md` §3.
- **Branch → PR → merge. No pushing to `main`.** `pr-checks.yml` runs deterministic
  gates on every PR (free). For a review, ask Claude Code / `/code-review` to look
  at the branch before merge — the gh-aw auto-reviewer is documented in
  `docs/BUILD-PLAYBOOK.md` §5 but deliberately not installed (needs paid API key).
- After any migration: `python sync/schema_snapshot.py` and commit
  `docs/schema-snapshot.json` (CI fails the PR if it's stale) + note new
  tables/columns here.

## Common commands

```bash
# run a sync locally (needs the env vars); --dry-run prints without writing
cd sync && pip install -r requirements.txt && python sync.py
python onchain.py --dry-run

# regenerate the schema snapshot after a migration
python sync/schema_snapshot.py

# trigger a scheduled workflow now
gh workflow run sync.yml        # IBKR
gh workflow run onchain.yml     # Zerion wallet

# recompile the gh-aw review workflow after editing pr-review.md
gh aw compile

# preview the dashboard
python3 -m http.server 8000 --directory app
```
