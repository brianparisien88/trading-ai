# Build Playbook — GitHub Pages + Supabase + Claude Code

Reusable patterns distilled from the Trading Dashboard build. Copy this file into
any new project of the same shape (static dashboard, Supabase backend, a
scheduled sync job, Claude Code doing the work). Adapt the specifics; keep the
rules.

---

## 0. Architecture (the shape)

```
external source(s)  ──►  sync job (Python, GitHub Actions cron)  ──►  Supabase (Postgres + RLS)
                              deterministic, NO LLM at runtime            │
                                                                         ▼
                                          static dashboard (one HTML file, GitHub Pages)
                                          reads Supabase REST directly, publishable key
```

- **Deterministic ETL.** The sync is plain code. No LLM call in the runtime path
  — an LLM makes the pipeline non-reproducible and adds a failure mode you can't
  debug. Claude Code writes and changes the sync; it never *is* the sync.
- **One dashboard file** (`app/index.html`), vanilla JS, `supabase-js` from CDN.
  No build step. It reads the DB live with the publishable key + RLS.
- **The sync is the only writer.** The dashboard writes only the handful of
  user-editable columns, gated by auth.

## 1. Secrets — do this first, every time

- **Before creating any file that could hold a secret**, confirm `.gitignore`
  contains `.env` and `.env.*`. This is step zero, not step ten.
- Local: `.env` (gitignored) + `.env.example` (committed, placeholder values).
- CI: GitHub → Settings → Secrets and variables → Actions. Secrets for
  credentials, Variables for non-secret config (query ids, wallet addresses,
  feature flags).
- **Supabase uses the new key system**: `sb_publishable_…` (= `anon` role, RLS
  applies, safe in client HTML) and `sb_secret_…` (bypasses RLS, server-only,
  never in `app/` or any committed file). Don't use `anon`/`service_role`
  naming or pre-2026 client patterns.
- The publishable key **is** meant to be in the client HTML. A `sb_secret_`
  value there is a security incident.

## 2. Supabase schema conventions

- Every table: `enable row level security` + a `for select using (true)` public
  read policy. No public writes.
- User-editable fields (notes, tags, plans): a **column-scoped** grant +
  policy —
  ```sql
  grant update (note, tag, planned_x) on my_table to authenticated;
  create policy "auth edits its own fields" on my_table
    for update to authenticated using (true) with check (true);
  ```
- **Sync-owned vs user-owned columns.** The sync's upsert payload **omits** the
  user columns entirely, so an upsert can never stomp what the user typed. Write
  this down in `CLAUDE.md` as a hard rule.
- Single-row tables (a "current summary"): PK `id text default 'current'`.
- Stable primary keys. Derive them from stable natural data
  (`{order_a}_{order_b}_{n}`), not an enumerate index that shifts when the
  upstream list changes. If the key formula changes, every row churns — fine for
  a derived table with no user data, fatal if users have attached notes.
- Migrations live in `supabase/migrations/`, applied by hand (or MCP). Keep them
  small and named for what they do.
- Regenerate `docs/schema-snapshot.json` (`python sync/schema_snapshot.py`)
  after every migration and commit it — CI fails the PR otherwise.

## 3. Sync job — the rules that came from real incidents

- **Never let an empty/failed fetch mutate the database.** If the upstream
  returns nothing, that's almost always a transient failure, not "the data is
  gone." Guard:
  - `die`/abort if a fetch that normally returns rows returns zero.
  - Run the stale-row cleanup (`DELETE WHERE synced_at < :now`) **only on tables
    you actually wrote to this run.**
  - Never `DELETE … WHERE id NOT IN (<all ids>)` with a big list — it blows the
    URL length. Use the `synced_at` watermark.
- **Transient vs real failure.** Rate limits, 5xx, timeouts, empty bodies →
  raise a `TransientError`, catch it in `main()`, log, and **exit 0**. The
  scheduled run stays green, the tables are untouched, the next run recovers,
  and you don't get paged. Only real bugs exit non-zero.
- **HTTP helper raises, never returns empty.** A `get()` that returns `{}` on
  exhausted retries silently poisons everything downstream. Raise.
- **Idempotent upserts.** `Prefer: resolution=merge-duplicates`, chunk large
  batches (~500 rows). Re-running the sync twice must be a no-op.
- **One workflow per data source.** If you pull from two providers, give each
  its own workflow file and schedule. A hiccup in one must not fail the other.
- **`--dry-run`** flag on every sync that prints what it would write. Use it for
  local iteration; do **not** burn CI runs (and third-party rate limits) to test.
- Label currency / units on every money figure and never combine unlabeled ones.

## 4. Dashboard conventions

- Map DB row → render shape in one `mapX(row)` function per table.
- `loadData()` does `Promise.all` of the table reads. Exclude bulky JSONB
  columns from the bulk read; lazy-fetch them per row on expand.
- **PostgREST caps responses at 1000 rows.** If a table can exceed that,
  paginate (`.range(from, from+999)` loop) or compute the aggregates you need
  server-side into the summary table.
- Aggregates for ribbons/tiles: compute in the sync, store in the summary row.
  Don't recompute across thousands of client-side rows.
- Theme, empty states, and "synced N ago" on every view.

## 5. Git & CI discipline

- **Branch → PR → merge. No pushing to `main`.** (We learned this the hard way.)
- `pr-checks.yml` — deterministic gates, **free, no API key**: code compiles,
  schema snapshot current, diff scanned for unguarded destructive ops. This is
  the part that earns its keep on a solo repo — keep it.
- **Independent review:** for a solo dev who's in the loop, just ask Claude Code
  (or `/code-review`) to review the branch before merging — uses your existing
  subscription, you decide when it's worth it. A fresh session with no memory of
  the authoring work is genuinely independent for logic/safety bugs.
- **Only** automate that review (gh-aw `pr-review.md`, triggered on every PR)
  when you want it hands-off and unattended — it needs a separate pay-as-you-go
  `ANTHROPIC_API_KEY` (~$1–5/mo) and a spend cap. `safe-outputs: add-comment`
  only, no write token to the agent, advisory. `gh extension install
  githubnext/gh-aw` → author `.github/workflows/pr-review.md` → `gh aw compile` →
  commit the `.lock.yml`. Skip it until the free checks prove insufficient.
- Pin action versions; let Dependabot bump them.
- End commit messages with the `Co-Authored-By` trailer.

## 6. GitHub-side setup checklist (one-time, per repo)

1. Settings → Secrets and variables → Actions → add whatever the sync needs
   (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, provider tokens, …).
2. Settings → Branches → add a ruleset on `main`: require a PR, require the
   `checks` status check to pass.
3. Pages: Settings → Pages → Source = GitHub Actions. `pages.yml` deploys `app/`
   only.
4. *(Only if adopting the gh-aw auto-review)* add `ANTHROPIC_API_KEY` secret;
   Settings → Actions → General → allow Actions to create PRs; `gh extension
   install githubnext/gh-aw`.

## 7. What Claude Code should keep in its own memory

- The architecture decisions that aren't visible in the code (why no LLM at
  runtime, why one workflow per source, currency conventions).
- Incidents and their root causes (so the same bug isn't reintroduced).
- Anything deferred with a date.
- Not: code structure, past fixes already in git, things `CLAUDE.md` covers.
