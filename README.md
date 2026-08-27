# trading-ai

Trade journal: IBKR options trades reconstructed into a round-trip ledger,
stored in Supabase, shown on a static dashboard.

## Layout

| Path | What |
|---|---|
| `docs/` | Build briefs, session changelog, schema draft, strategy spec, prototype dashboard |
| `supabase/` | Database schema + migrations ([`supabase/README.md`](supabase/README.md)) |
| `.env.example` | Env var template (copy to `.env`, which is gitignored) |

## Status

- [x] Repo + Supabase project created
- [x] Schema written — `supabase/migrations/20260827120000_init_trade_journal.sql`
- [x] Schema applied to the Supabase project (2026-08-27) — tables live, RLS verified
- [x] Dashboard rebuilt to read live from Supabase (`docs/trade_journal_dashboard.html`) — supabase-js, publishable key, email/password login for journal edits
- [ ] User creates the auth login user + disables public signups (`supabase/README.md`)
- [ ] Deterministic IBKR sync script + GitHub Actions cron
- [ ] Dashboard deployed to GitHub Pages
- [ ] (Deferred) Historical 242-trade migration

## Architecture

Deterministic ETL, no LLM at runtime. Daily: IBKR Client Portal API → merge fills →
FIFO-pair → upsert to Supabase (secret key). Dashboard reads Supabase directly from
the browser (publishable key + RLS). See `docs/` for the full decision log.
