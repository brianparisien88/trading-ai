# Build Brief Addendum — 2026-08-27

**Supersedes relevant sections of `build_brief_2026-08-26_scope_lock.md`. Read both.**

## Changes since the original brief

| Area | Original plan | Updated |
| :---- | :---- | :---- |
| **Historical data (242 trades)** | Migrate now, in this build phase | **Deferred.** Not required for initial build. Schema, live sync, and dashboard should be built and working first; historical backfill is a separate later task. Do NOT attempt to source historical fills data from chat transcripts or hand-typed data — when this is picked back up, it will come from a proper IBKR export or the production sync script itself. |
| **Supabase API keys** | Referred to as `anon` / `service_role` (Supabase's old naming) | **Project uses Supabase's new key system.** Keys are named `sb_publishable_...` (replaces `anon`) and `sb_secret_...` (replaces `service_role`). Same purpose and security model — publishable key is safe for client-side/RLS-protected reads, secret key is server-only and bypasses RLS — but the naming, and possibly some client library syntax, differs from most Supabase tutorials and pre-2026 documentation. **Do not default to `anon`/`service_role` naming or older client patterns — verify against current Supabase docs/SDK for this project.** |
| **GitHub repo** | To be created | **Done.** Repo is `github.com/brianparisien88/trading-ai`, cloned locally to `~/Desktop/COWORK/Trading-AI`. Contains `README.md` (auto-created by GitHub) and a `docs/` folder with the reference files listed below. Git is authenticated via GitHub CLI (`gh`) using OAuth — `git push`/`git pull` work without credential prompts. |
| **Supabase project** | To be created | **Done.** Project is live (region: us-west-2). Publishable and secret keys generated and saved locally by the user (not in any chat). |

## Critical setup step before Claude Code writes any code

**Add `.env` to `.gitignore` immediately**, before any `.env` file is created in the repo. The user will do this manually before starting the Claude Code session, but Claude Code should verify `.gitignore` contains `.env` (and ideally `.env.*` except `.env.example`) as its very first action, before creating any file that could hold secrets.

## Reference files now available locally

All in `~/Desktop/COWORK/Trading-AI/docs/`:

- `build_brief_2026-08-26_scope_lock.md` (as `.docx`)  
- `living-state-global-memory` (as `.docx`)  
- `multi-timeframe-divergence-strategy-spec` (as `.docx`)  
- `session_changelog_2026-08-26.md`  
- `supabase_schema_draft.md`  
- `trade_journal_dashboard.html`  
- `trade_pipeline.py`

This addendum should also be downloaded into that `docs/` folder and pushed to GitHub before starting Claude Code.

## Updated immediate next actions

- [x] ~~User creates Supabase project~~ — done  
- [x] ~~User creates GitHub repo, connects local git~~ — done  
- [ ] User adds `.env` to `.gitignore` locally, commits, pushes  
- [ ] User downloads this addendum into `docs/`, commits, pushes  
- [ ] Start Claude Code session in `~/Desktop/COWORK/Trading-AI`, pointed at local `docs/` files  
- [ ] Claude Code: stand up the `closed_trades` / `open_positions` schema in Supabase per `supabase_schema_draft.md`  
- [ ] Claude Code: rebuild dashboard to read live from Supabase REST API using the new publishable/secret key pattern, replacing baked-in JSON / `window.storage`  
- [ ] (Deferred) Historical data migration — revisit later, not blocking  
- [ ] (Deferred) IBKR Client Portal Web API registration \+ deterministic sync script — separate from Claude's IBKR MCP connector, needed before the daily-sync step specifically, not before schema/dashboard work

