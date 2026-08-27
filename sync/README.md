# sync/ — IBKR → Supabase

Deterministic daily ETL. No LLM at runtime, no interactive login.

```
IBKR Flex Web Service (XML)
        │  fetch (token + query id)
        ▼
flex_to_raw_trades / flex_to_raw_positions      sync.py
        │  reshape to the dicts trade_pipeline expects
        ▼
run_pipeline()  ── merge fills → FIFO-pair → enrich → derive   trade_pipeline.py
        │
        ▼
Supabase (secret key, bypasses RLS)
  • closed_trades   upsert on id   (journal columns never touched)
  • open_positions  upsert current + delete stale
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

Scheduled: `.github/workflows/sync.yml` runs it daily at 23:00 UTC (and on the
manual **Run workflow** button).

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
  sync-owned columns, so `strategy` / `journal_thoughts` / `planned_stop` /
  `planned_target` / `contract_description` are never overwritten.
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

## Not done yet

- `contract_description` on `closed_trades` stays null. Flex carries strike/expiry
  on executions, so capture-on-close is now easy to add — tracked as a follow-up,
  not wired in here.
