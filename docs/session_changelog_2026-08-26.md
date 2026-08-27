# Trade Journal Dashboard — Session Changelog
**Date:** 2026-08-26
**Focus:** Prototype build (Claude artifact) + production architecture planning

---

## 1. Key Decisions & Architectural Changes

- **Database/storage platform: Supabase (Postgres)**, chosen over Google Sheets+Zapier,
  Airtable, and Make.com. Rationale: free tier (500MB storage, ~2-5GB bandwidth,
  confirmed live 2026-08-26) is far more than sufficient for this data volume; real
  SQL enables cross-cutting analytics a spreadsheet can't; same Postgres project can
  later host RAG/pgvector tables — direct career relevance since user is training as
  an AI Automation Specialist/Architect. Free-tier gotcha to verify at signup:
  historically pauses after ~1 week of zero API activity — daily sync should keep it
  warm automatically.
- **Make.com evaluated and rejected for THIS build specifically** (not rejected in
  general) — IBKR has no native Make.com connector, so Make's core value (pre-built
  app connectors) doesn't apply here; would add a vendor/security surface with no
  payoff. Valuable as a SEPARATE deliberate learning exercise (building a custom
  HTTP/OAuth module against IBKR's raw Client Portal API) given user's career goals —
  tracked as its own backlog item, not blocking this build.
- **Core architecture philosophy: deterministic-before-agentic applies to the user's
  own infrastructure choices, not just to Make.com scenario design internals.** The
  daily trade sync (pull → merge → FIFO-pair → write) has zero judgment calls in it
  anywhere. It should run as a plain scheduled script/serverless function (e.g. a
  Supabase Edge Function + Supabase's own cron, or GitHub Actions), NOT as a
  recurring Claude/Cowork "task" and NOT handed to a client's LLM agent to run daily.
  Reasons: avoids requiring a standing LLM subscription for rote ETL; avoids giving
  an LLM agent standing unattended credentials to a brokerage account + database
  every day; avoids non-determinism in a process that should be boring and auditable.
  **Claude/Cowork's agentic role is reserved for genuine judgment work**: reviewing
  ambiguous trade-pairing flags, analyzing journal entries for trends, retrospective
  "why did this month underperform" style questions — queried on-demand via a direct
  Supabase MCP connection once that's set up.
- **Dashboard hosting: leaning Vercel or Cloudflare Pages** (both free indefinitely
  at this scale, both support a custom domain, both integrate with GitHub for
  auto-deploy). GitHub Pages is the fully-free fallback if zero vendor dependency is
  preferred. Not yet finalized.
- **Client-handoff model** (for future paid work, not just this personal build):
  build phase uses Claude Code/Cowork to construct the deterministic pipeline script
  (exactly the work done this session — building, testing, catching edge cases);
  the client receives a real scheduled script/function + their own hosted dashboard +
  their own database — NOT a transferred "Claude skill/task" that requires an
  ongoing LLM subscription just to do routine ETL. The client's own AI/LLM is
  reserved for judgment-requiring features only. This is framed as a genuine
  differentiator for an AI-automation-architect positioning: knowing when NOT to
  use an agent is the more senior, more sellable instinct.
- **Security baseline agreed for the production build:**
  - Dashboard uses Supabase's scoped `anon` key + Row-Level Security policies —
    NEVER the service-role key client-side.
  - The scheduled sync function uses a separate, narrowly-scoped service key with
    write access limited to the specific tables it needs.
  - Client (in a client-work context) authenticates their OWN brokerage/Supabase/
    hosting accounts — nothing of the builder's ever gets handed over.
  - Secrets live in the hosting platform's environment variables, never hardcoded
    into code that gets handed off.

## 2. Technical Checkpoints & Verified Patterns

See `trade_pipeline.py` for the fully implemented, standalone, tested version of
this logic (verified to reproduce this session's exact numbers: 501 raw fills →
493 merged orders → 242 closed round-trip trades, matching to the cent).

- **Multi-lot order merge is required before FIFO pairing.** IBKR sometimes reports
  one multi-contract order as several 1-lot fill rows sharing the same `order_id`
  (e.g. a 2-contract entry, matching this account's locked "two-contract minimum
  for partial profit-taking" rule). Must merge fills sharing an `order_id` BEFORE
  attempting to pair opens/closes, or lot sizes and counts will be wrong.
- **FIFO pairing works cleanly for the vast majority of trades.** Of 242
  reconstructed round trips, only 6 needed an "ambiguous" flag (closed against
  multiple separate opens same day). These are marked with a `reason` field and
  should be shown to the user for manual verification against the broker — never
  silently trust an ambiguous pairing.
- **Commission must be attributed on BOTH the entry and exit leg**, proportionally
  when a close is split across multiple FIFO legs. An earlier draft of this pipeline
  only attributed exit-side commission and undercounted total costs by roughly half
  — this was caught and fixed; the corrected version reconciles to the cent against
  raw fill data ($523.05 total commission).
- **Live position data (`get_account_positions`) is ALWAYS authoritative for what's
  currently open and at what real cost basis** — use its `average_price` field
  (which already includes commission) for entry price, not a FIFO-reconstructed
  guess from trade history. This also sidesteps needing FIFO matching for price at
  all on open positions.
- **Trade-history data (`get_account_trades`) lags behind live position data**,
  sometimes by a day or more. Observed directly this session: a position closed
  live in the morning (CELH) didn't show as closed in trade history until the next
  sync; a position opened live the same morning (SOFI) didn't appear in trade
  history at all yet, showing as a live position with no matching FIFO-reconstructed
  open lot. Both self-resolve on the next sync — this is normal clearing lag, not a
  bug, and should not be "fixed" by guessing.
- **No option greeks (delta/gamma/theta/vega) are available** via the connected
  IBKR MCP tools — only price, implied volatility (`option_midpoint_iv`), volume
  (`option_volume`), and open interest (`option_open_interest`) via
  `get_price_snapshot`. If real delta is needed, it will require a different data
  source (FMP's options endpoints were flagged as worth checking, unverified).
- **Historical closed trades have NO contract-level detail (strike/expiry)** — only
  the underlying ticker symbol. That detail exists ONLY in the live position
  snapshot while a position is open. To preserve it for future closed trades, the
  pipeline needs to capture `contract_description` from the live position data at
  the moment a position closes (this is a documented TODO in `trade_pipeline.py`,
  not yet implemented — requires diffing consecutive daily position snapshots to
  detect a close event).
- **Currency mismatch**: account-level balances (net liquidation, buying power,
  from `get_account_summary`) are denominated in the account's home currency
  (CAD, in this case), while position-level dollar figures (P&L, cost basis) are in
  the contract's trading currency (USD, for US-listed options). These must always
  be labeled explicitly and never combined into a single unlabeled figure.
- **Per-trade "Cum. PF" (cumulative profit factor) was tried and rejected as a
  per-row metric** — it describes system-wide health at a point in time (running
  gross-wins ÷ gross-losses in chronological order), not that individual trade's
  quality, and was genuinely confusing when attached to a single trade row (a
  standout winning trade showed a mediocre-looking PF because the metric wasn't
  about that trade). Replaced with **Return %** (P&L ÷ capital at risk / cost
  basis) as the per-trade metric — directly meaningful, computable with no
  additional input.
- **True Risk:Reward (planned, e.g. "3:1") cannot be computed from broker data
  alone** — it's a planning concept (intended stop/target at time of entry), and
  the broker only records what actually happened. Solution: added optional
  "Planned Stop" / "Planned Target" fields to the journal entry, so the trader's
  intent gets captured at logging time and both planned and realized R:R can be
  shown (`Planned R:R = 1:X · actual outcome: YR`).
- **`window.storage` (Claude artifact's built-in key-value store) is not reliable
  long-term storage.** A real transient save failure was observed mid-session
  ("Storage set failed: Internal server error"). Mitigated in the prototype with
  3-attempt retry+backoff and explicit failure UI state, but the real fix is
  moving journal data to Supabase once that's built — `window.storage` should be
  treated as prototype-only, not production-durable.

## 3. State of the System / Project

- **Prototype dashboard**: built and iterated as a Claude HTML artifact across this
  session. Two tabs (Open Trades, Trade History), live IBKR data baked in per-sync,
  journal entries (Strategy / Journaling Thoughts / Planned Stop / Planned Target)
  currently in `window.storage`. Saved as a **code-only template** (data arrays
  emptied intentionally) to Google Drive TRADING-AI folder as
  `trade_journal_dashboard.html` — see file for full design/logic; re-populate
  `CLOSED_RAW`/`OPEN_RAW` with fresh pipeline output each time it's used.
- **Reconstruction pipeline**: fully implemented, tested, and saved standalone as
  `trade_pipeline.py` (see Section 2). This is now independent of any specific
  chat session — can be run directly against exported IBKR JSON, or adapted into
  a Supabase Edge Function once that infrastructure exists.
- **Production infrastructure**: NOT yet started. No Supabase project created. No
  dashboard hosting selected or deployed. No sync script deployed anywhere (only
  exists as the standalone `trade_pipeline.py` file, not yet wired to run on a
  schedule).
- **Real trading performance observed in the underlying data** (flagged for the
  user's own review, not yet acted on): 37.6% win rate, ~$71 realized P&L before
  commissions, ~$523 in total commissions, across 242 closed trades over ~5 months
  — a trade frequency (roughly 1.5 closes/day) that looks inconsistent with the
  account's stated 1-week+ swing-holding framework. Worth a deliberate Discernment
  review of whether actual trading behavior matches the intended system.

## 4. Active Backlog & Next Action Items

- [ ] Resolve IBKR daily-pull architecture: fully independent API integration
      (real deterministic script hitting IBKR's raw Client Portal API directly,
      more setup) vs. a minimal Cowork-triggered raw-data-pull hybrid (Claude does
      only the IBKR fetch, all transform logic stays in `trade_pipeline.py`) —
      needs dedicated research before deciding.
- [ ] Create Supabase account/project; design a minimal schema (see
      `supabase_schema_draft.md`) — keep deliberately simple, resist
      over-normalizing just because it's "a real database now."
- [ ] Migrate the existing reconstructed ledger (via `trade_pipeline.py` output)
      into Supabase.
- [ ] Decide and set up dashboard hosting (Vercel vs. Cloudflare Pages vs.
      GitHub Pages).
- [ ] Rebuild the dashboard to read live from Supabase (anon key + RLS) instead
      of baked-in JSON / `window.storage`.
- [ ] Wire `trade_pipeline.py` into an actual scheduled job (Supabase Edge
      Function + cron, or GitHub Actions) instead of running it manually.
- [ ] Set up the Supabase MCP connector for Claude, for on-demand agentic
      journal/trend analysis once real data lives there.
- [ ] Implement the contract-detail-capture TODO in `trade_pipeline.py` (preserve
      strike/expiry for trades closed going forward, via position-snapshot diffing).
- [ ] Separate, deliberate follow-on project (career skill-building, not part of
      the production path above): build a Make.com scenario with a custom
      HTTP/OAuth module against IBKR's raw API.
