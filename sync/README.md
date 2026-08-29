# sync/ — IBKR → Supabase

Deterministic daily ETL. No LLM at runtime, no interactive login.

```
IBKR Flex Web Service (XML)          Yahoo Finance chart API
        │  fetch (token + query id)          │  1 call / ticker (keyless)
        ▼                                     │
flex_to_raw_* → run_pipeline()                │
   merge fills → FIFO-pair → derive           │
        │                                     ▼
        └──► enrich: contract detail, trade_seq, underlying prices
        ▼
Supabase (secret key, bypasses RLS)
  • closed_trades    upsert on id   (journal columns never touched)
  • open_positions   upsert current + delete stale
  • account_summary  single row
```

## Files

| File | |
|---|---|
| `sync.py` | The job. `python sync.py`. |
| `trade_pipeline.py` | Reconstruction logic (merge → FIFO-pair → enrich → derive). Tested standalone; also usable as `python trade_pipeline.py trades.json positions.json`. |
| `requirements.txt` | `requests`, `tzdata`. |

## Run it

Locally:

```bash
cd sync
pip install -r requirements.txt
FLEX_TOKEN=... SUPABASE_URL=... SUPABASE_SECRET_KEY=... python sync.py
```

Scheduled: `.github/workflows/sync.yml` runs it daily at **12:00 UTC** (~08:00 ET),
after IBKR's overnight batch finalises the prior trading day's Flex statement, plus
the manual **Run workflow** button.

## GitHub setup (one time)

Repo **Settings → Secrets and variables → Actions**:

**Secrets:**
| Name | Value |
|---|---|
| `FLEX_TOKEN` | IBKR Flex Web Service token (Client Portal → Performance & Reports → Flex Queries → Flex Web Service Configuration → **Generate New Token**) |
| `SUPABASE_URL` | `https://wcbokczlllatengdrdes.supabase.co` |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` |

**Variables:**
| Name | Value |
|---|---|
| `FLEX_QUERY_ID` | `1618686` (the "TradeJournalSync" Activity Flex Query) |

> Generate the Flex token fresh here rather than reusing one that's been shown
> on screen. It's read-only and account-report-scoped, but still a credential.
> IBKR Flex tokens last ~1 year — set a reminder to rotate before it expires
> (the job will fail loudly with `code 1012` when it does).

## Config knobs (env vars)

| Var | Default | |
|---|---|---|
| `FLEX_QUERY_ID` | `1618686` | Activity Flex Query id |
| `FLEX_TIMEZONE` | `America/New_York` | tz IBKR trade timestamps are in, converted to UTC on write |

## Behaviour notes

- **Journal columns are safe.** The `closed_trades` upsert payload only includes
  sync-owned columns (see `CLOSED_SYNC_COLUMNS`), so `strategy` /
  `journal_thoughts` / `planned_stop` / `planned_target` are never overwritten.
- **Stable ids.** `closed_trades.id` is `{entry_order}_{exit_order}_{n}` and does
  not shift when new trades appear, so daily re-runs are idempotent.
- **Empty-guard.** If Flex returns zero trades *and* zero positions the job
  aborts instead of wiping the tables.
- **Ambiguous pairings** (`ambiguous = true`) are written through with a reason
  and surfaced in the dashboard — verify them against the broker, never trust
  silently.
- **Window.** The Flex query returns the last 365 calendar days each run; the
  pipeline + dedup handle overlap. Full historical backfill is a separate,
  still-deferred task.

### Enrichment (added 2026-08-29)

- **Contract detail** (`contract_expiry` / `contract_type` / `contract_strike` /
  `contract_description`) is read straight off the Flex execution records, keyed
  by order id. Real data, not a guess — so it also backfills historical trades.
- **`trade_seq`** = the Nth closed round-trip, chronological by exit.
- **Underlying stock prices** (`underlying_price_entry` / `_exit` / `_latest` +
  `_latest_at`) come from Yahoo Finance's chart API — one keyless call per unique
  ticker, gives full daily-close history + the current price. Best-effort: a
  ticker Yahoo can't resolve just leaves those fields null; the sync never fails
  over price data. Entry/exit use the close on or before that date (walks back
  over weekends/holidays); latest is refreshed every run.
- **Contract-aware "If Held"**: `underlying_price_horizon` is the underlying
  close at `min(contract_expiry, today)`, and `underlying_price_peak` /
  `underlying_peak_date` are the most favourable close between exit and that
  horizon (highest for a call, lowest for a put). So the dashboard's "If Held"
  and "Verdict" measure the move over *the contract's actual life*, not "to
  today regardless of when it expired". Directional proxy, not an options P&L.
