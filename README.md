# trading-ai

Trade journal: IBKR options trades reconstructed into a round-trip ledger,
stored in Supabase, shown on a static dashboard.

## Layout

| Path | What |
|---|---|
| `app/` | The dashboard (`index.html`). Only this folder is deployed to GitHub Pages. |
| `supabase/` | Database schema + migrations ([`supabase/README.md`](supabase/README.md)) |
| `docs/` | Build briefs, session changelog, schema draft, strategy spec — reference only, **not** web-served |
| `.github/workflows/pages.yml` | Publishes `app/` to Pages on push |
| `.env.example` | Env var template (copy to `.env`, which is gitignored) |

## Status

- [x] Repo + Supabase project created
- [x] Schema written + applied (2026-08-27) — `supabase/migrations/`, tables live, RLS verified
- [x] Dashboard rebuilt to read live from Supabase (`app/index.html`) — supabase-js, publishable key, email/password login for journal edits
- [ ] User creates the auth login user + disables public signups (`supabase/README.md`)
- [ ] Turn on GitHub Pages (Settings → Pages → Source: **GitHub Actions**)
- [ ] Deterministic IBKR sync script + GitHub Actions cron
- [ ] (Deferred) Historical 242-trade migration

## Architecture

Deterministic ETL, no LLM at runtime. Daily: IBKR Client Portal API → merge fills →
FIFO-pair → upsert to Supabase (secret key). Dashboard reads Supabase directly from
the browser (publishable key + RLS). See `docs/` for the full decision log.
