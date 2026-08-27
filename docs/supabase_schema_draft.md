# Supabase Schema Draft — Trade Journal

**Status:** Draft only — not yet built. Intentionally minimal; expand only when a
real need shows up, not preemptively. Two tables, matching the two dashboard tabs.

---

## Table: `closed_trades`

One row per reconstructed round-trip trade. Written once by the sync job (never
updated after insert, except by the user's own journal edits) — the `id` field is
the natural dedup key so the sync can safely run daily without creating duplicates.

| Column | Type | Notes |
|---|---|---|
| `id` | `text primary key` | From `trade_pipeline.py`: `{entry_order}_{exit_order}_{n}`. Stable across syncs — use for `WHERE NOT EXISTS` dedup on insert. |
| `symbol` | `text` | Underlying ticker. |
| `entry_time` | `timestamptz` | Nullable — null if no matching open was found. |
| `entry_price` | `numeric` | Nullable. |
| `exit_time` | `timestamptz` | |
| `exit_price` | `numeric` | |
| `size` | `numeric` | Contracts. |
| `pnl` | `numeric` | Realized P&L, this leg. |
| `commission` | `numeric` | Entry + exit commission, attributed. |
| `cost_basis` | `numeric` | Nullable. |
| `return_pct` | `numeric` | Nullable. `pnl / cost_basis * 100`. |
| `days_held` | `numeric` | Nullable. |
| `ambiguous` | `boolean` | From FIFO pairing — true if this leg needs manual verification. |
| `ambiguous_reason` | `text` | Nullable. |
| `entry_order_id` | `bigint` | Nullable — IBKR order id, for traceability. |
| `exit_order_id` | `bigint` | Nullable. |
| `strategy` | `text` | Nullable. User-entered. |
| `journal_thoughts` | `text` | Nullable. User-entered. |
| `planned_stop` | `numeric` | Nullable. User-entered. |
| `planned_target` | `numeric` | Nullable. User-entered. |
| `contract_description` | `text` | Nullable. Strike/expiry — only populatable for trades closed AFTER the capture-on-close feature is built (see pipeline TODO). Historical trades will stay null; don't backfill with guesses. |
| `synced_at` | `timestamptz` | When this row was written/last touched by the sync job. |

**Row-Level Security:** single-user for now — policy can simply be
`auth.uid() = owner_id` once auth is added, or left fully open behind the `anon`
key if this stays single-tenant personal use. Revisit if this ever serves more
than one person.

---

## Table: `open_positions`

One row per currently-open position. Fully truncated and rewritten by the sync
job each run — no history needed here, only current state. (If a history of what
was open on past dates becomes useful later, that's a `snapshot_date` column +
no-truncate insert instead — not needed yet.)

| Column | Type | Notes |
|---|---|---|
| `id` | `text primary key` | `open_{contract_id}` from live IBKR position data. |
| `symbol` | `text` | |
| `contract_description` | `text` | Full detail incl. strike/expiry — always available for OPEN positions (unlike closed_trades). |
| `entry_time` | `timestamptz` | Nullable — null if `unverified_entry_date`. |
| `entry_price` | `numeric` | From IBKR's live `average_price` (includes commission) — authoritative, not reconstructed. |
| `cost_basis` | `numeric` | |
| `market_value` | `numeric` | |
| `unrealized_pnl` | `numeric` | |
| `daily_pnl` | `numeric` | |
| `dte` | `integer` | Nullable. Days to expiry. |
| `iv` | `numeric` | Nullable. |
| `volume` | `integer` | Nullable. |
| `open_interest` | `integer` | Nullable. |
| `unverified_entry_date` | `boolean` | True if no FIFO match found (see pipeline docs — happens when a position opened outside the fetched trade-history window). |
| `synced_at` | `timestamptz` | |

---

## Explicitly NOT in scope yet (avoid over-building)

- No separate `strategies` lookup table — `strategy` stays a free-text/enum-ish
  column on `closed_trades` until there's an actual need to query/manage it
  as its own entity.
- No `users` table / multi-tenancy — single account, single user, for now.
- No audit/history table for `open_positions` changes — add only if "what did
  my open positions look like last Tuesday" becomes an actual question worth
  answering.
- No RAG/vector tables — those belong in this same Supabase project eventually,
  but as entirely separate tables (e.g. `documents`, `embeddings`), unrelated to
  this schema. Don't conflate the two builds.
