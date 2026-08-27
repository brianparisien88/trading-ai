# trading-ai

Trade journal: IBKR options trades reconstructed into a round-trip ledger,
stored in Supabase, shown on a static dashboard.

## Layout

| Path | What |
|---|---|
| `app/` | The dashboard (`index.html`). Only this folder is deployed to GitHub Pages. |
| `sync/` | Daily IBKR → Supabase ETL ([`sync/README.md`](sync/README.md)) |
| `supabase/` | Database schema + migrations ([`supabase/README.md`](supabase/README.md)) |
| `docs/` | Build briefs, session changelog, schema draft, strategy spec — reference only, **not** web-served |
| `.github/workflows/` | `pages.yml` deploys `app/`; `sync.yml` runs the daily sync |
| `.env.example` | Env var template (copy to `.env`, which is gitignored) |

## Status

- [x] Repo + Supabase project created
- [x] Schema written + applied (2026-08-27) — `supabase/migrations/`, tables live, RLS verified
- [x] Dashboard rebuilt to read live from Supabase (`app/index.html`) — supabase-js, publishable key, email/password login for journal edits
- [x] Auth login user created + public signups disabled
- [x] GitHub Pages live — https://brianparisien88.github.io/trading-ai/ (Actions deploy from `app/`)
- [x] Deterministic IBKR sync (`sync/`) + daily GitHub Actions cron — **needs repo secrets set** (`sync/README.md`)
- [ ] (Deferred) Historical 242-trade migration
- [ ] (Follow-up) Capture `contract_description` on closed trades

## Architecture

Deterministic ETL, no LLM at runtime. Daily GitHub Action: pull IBKR Flex Web
Service report → merge fills → FIFO-pair → upsert to Supabase (secret key).
Dashboard reads Supabase directly from the browser (publishable key + RLS),
with Supabase Auth for journal edits. See `docs/` for the full decision log.
