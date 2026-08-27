"""
Trade Journal Pipeline
=======================
Reconstructs round-trip options trades from raw IBKR fill data, and cross-checks
against live position data. Built and validated interactively in Claude chat
on 2026-08-26 against a real IBKR account (~500 fills, 5 months of history).

INPUT DATA SHAPE (from IBKR MCP tools)
---------------------------------------
raw_trades: list of dicts from `Interactive Brokers (IBKR):get_account_trades`
    Each fill has: trade_id, symbol, sec_type, currency, side ("BUY"/"SELL"),
    size, price, trade_time (ISO8601 UTC), commission, realized_pnl, order_id.
    NOTE: does NOT include strike/expiry/contract detail for closed trades --
    only the underlying ticker. Contract detail is only available on OPEN
    positions (see raw_positions below).

raw_positions: list of dicts from `Interactive Brokers (IBKR):get_account_positions`
    Each open position has: contract_id, contract_description (e.g.
    "AAL Nov20'26 19 CALL @AMEX"), position (size), market_price, market_value,
    average_price (cost basis INCLUDING commission -- this is the authoritative
    entry price, better than reconstructing from fills), unrealized_pnl,
    daily_pnl, currency.

PIPELINE STAGES
---------------
1. merge_fills_by_order()   -- IBKR sometimes reports one multi-contract order
                               as several 1-lot fill rows sharing the same
                               order_id (e.g. a 2-contract entry). Merge these
                               into single logical orders before FIFO pairing,
                               or you'll double-count / misjudge lot sizes.
2. fifo_pair_trades()       -- Walks each symbol's merged orders in chronological
                               order, matching BUY (open) lots to SELL (close)
                               lots FIFO-style. Produces round-trip trade records
                               with full entry+exit commission attribution.
                               Flags any close that didn't cleanly match a single
                               open lot as `ambiguous=True` with a `reason` --
                               these need manual verification against the broker,
                               never trust them silently.
3. enrich_open_positions()  -- Cross-references live IBKR positions against the
                               FIFO reconstruction. Live position data is ALWAYS
                               authoritative for what's actually open right now
                               (it updates instantly on fill; trade-history data
                               lags, sometimes by a day+). Use live average_price
                               for entry price, not the FIFO guess. Flags any
                               live position with no matching FIFO open lot as
                               `unverified_entry_date=True` (this happens when a
                               position was opened outside the fetched trade-
                               history window, or via an order not yet posted to
                               the trade-history endpoint).
4. compute_derived_fields() -- Adds days_held, return_pct (P&L as % of capital
                               at risk = cost basis), and cumulative profit
                               factor (chronological running gross-win/gross-loss
                               ratio -- a system-health metric, NOT a per-trade
                               quality metric; don't present it as grading an
                               individual trade).

KNOWN LIMITATIONS (do not silently paper over these)
-----------------------------------------------------
- No option greeks (delta/gamma/theta/vega) available from the connected IBKR
  MCP tools -- only price, IV, volume, open interest via get_price_snapshot.
- Historical closed trades have NO strike/expiry detail, only the ticker.
  If you want that detail preserved going forward, capture it from
  raw_positions at the moment a position closes (this pipeline does not
  currently do that -- it's a TODO, see bottom of file).
- Account-level balances (net liquidation, buying power) come from
  get_account_summary and are typically in the ACCOUNT currency (e.g. CAD),
  while position-level dollars (P&L, cost basis) are in the CONTRACT currency
  (e.g. USD for US options). Never merge these into one figure without
  labeling currency explicitly.
- `window.storage` (if journaling in a Claude artifact) is NOT reliable
  long-term storage -- observed a transient save failure in production.
  Treat any journal data (strategy, thoughts, planned stop/target) as
  needing a real database (see supabase_schema_draft.md) for anything
  that must not be lost.
"""

from collections import defaultdict, deque
from datetime import datetime, timezone
import re


# ---------------------------------------------------------------------------
# Stage 1: merge multi-lot fills sharing the same order_id
# ---------------------------------------------------------------------------
def merge_fills_by_order(raw_trades):
    by_order = defaultdict(list)
    for t in raw_trades:
        by_order[t["order_id"]].append(t)

    merged = []
    for oid, fills in by_order.items():
        fills_sorted = sorted(fills, key=lambda x: x["trade_time"])
        total_size = sum(f["size"] for f in fills_sorted)
        total_comm = sum(f.get("commission", 0) for f in fills_sorted)
        total_pnl = sum(f.get("realized_pnl", 0) for f in fills_sorted)
        wavg_px = (
            sum(f["price"] * f["size"] for f in fills_sorted) / total_size
            if total_size
            else fills_sorted[0]["price"]
        )
        merged.append(
            {
                "order_id": oid,
                "sym": fills_sorted[0]["symbol"],
                "side": fills_sorted[0]["side"][0],  # "B" or "S"
                "size": total_size,
                "px": round(wavg_px, 4),
                "time": fills_sorted[0]["trade_time"],
                "comm": round(total_comm, 2),
                "pnl": round(total_pnl, 2),
                "fill_count": len(fills_sorted),
                "trade_ids": [f["trade_id"] for f in fills_sorted],
            }
        )
    merged.sort(key=lambda x: x["time"])
    return merged


# ---------------------------------------------------------------------------
# Stage 2: FIFO-pair opens to closes per symbol
# ---------------------------------------------------------------------------
def fifo_pair_trades(merged_orders):
    by_sym = defaultdict(list)
    for o in merged_orders:
        by_sym[o["sym"]].append(o)
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x["time"])

    round_trips = []
    unmatched_opens = []

    for sym, ords in by_sym.items():
        open_queue = deque()
        for o in ords:
            if o["side"] == "B":
                open_queue.append(
                    {
                        "remaining": o["size"],
                        "orig_size": o["size"],
                        "entry_time": o["time"],
                        "entry_px": o["px"],
                        "entry_order": o["order_id"],
                        "entry_comm_per_unit": o["comm"] / o["size"] if o["size"] else 0,
                    }
                )
            elif o["side"] == "S":
                remaining_to_close = o["size"]
                legs = []
                if not open_queue:
                    round_trips.append(
                        {
                            "sym": sym,
                            "entry_time": None,
                            "entry_px": None,
                            "exit_time": o["time"],
                            "exit_px": o["px"],
                            "size": o["size"],
                            "pnl": o["pnl"],
                            "comm": o["comm"],
                            "entry_order": None,
                            "exit_order": o["order_id"],
                            "ambiguous": True,
                            "reason": "no matching open found",
                        }
                    )
                    continue
                while remaining_to_close > 1e-9 and open_queue:
                    lot = open_queue[0]
                    take = min(lot["remaining"], remaining_to_close)
                    legs.append(
                        {
                            "entry_time": lot["entry_time"],
                            "entry_px": lot["entry_px"],
                            "entry_order": lot["entry_order"],
                            "size": take,
                            "entry_comm": lot["entry_comm_per_unit"] * take,
                        }
                    )
                    lot["remaining"] -= take
                    remaining_to_close -= take
                    if lot["remaining"] <= 1e-9:
                        open_queue.popleft()
                partial_flag = remaining_to_close > 1e-9
                for leg in legs:
                    frac = leg["size"] / o["size"]
                    round_trips.append(
                        {
                            "sym": sym,
                            "entry_time": leg["entry_time"],
                            "entry_px": leg["entry_px"],
                            "exit_time": o["time"],
                            "exit_px": o["px"],
                            "size": leg["size"],
                            "pnl": round(o["pnl"] * frac, 2),
                            "comm": round(o["comm"] * frac + leg["entry_comm"], 2),
                            "entry_order": leg["entry_order"],
                            "exit_order": o["order_id"],
                            "ambiguous": partial_flag or len(legs) > 1,
                            "reason": (
                                "sold more than tracked open size"
                                if partial_flag
                                else ("closed against multiple separate opens" if len(legs) > 1 else None)
                            ),
                        }
                    )
        for lot in open_queue:
            unmatched_opens.append(
                {
                    "sym": sym,
                    "entry_time": lot["entry_time"],
                    "entry_px": lot["entry_px"],
                    "entry_order": lot["entry_order"],
                    "size": lot["remaining"],
                    "comm": round(lot["entry_comm_per_unit"] * lot["remaining"], 2),
                }
            )

    round_trips.sort(key=lambda x: x["exit_time"] or x["entry_time"] or "", reverse=True)
    # Stable id: {entry_order}_{exit_order}_{n}. `n` is scoped to the
    # (entry_order, exit_order) pair -- almost always 0 -- so the id does NOT
    # shift when unrelated trades are added on a later sync. (Earlier versions
    # used a global enumerate index here, which made ids churn every run and
    # broke the dedup-on-insert contract in supabase_schema_draft.md.)
    pair_seq = defaultdict(int)
    for r in sorted(round_trips, key=lambda x: (
        str(x["entry_order"]), str(x["exit_order"]),
        x["entry_time"] or "", x["exit_time"] or "", x["size"],
    )):
        key = (r["entry_order"] or "na", r["exit_order"] or "na")
        r["id"] = f"{key[0]}_{key[1]}_{pair_seq[key]}"
        pair_seq[key] += 1
    for i, o in enumerate(unmatched_opens):
        o["id"] = f"open_{o['entry_order']}_{i}"

    return round_trips, unmatched_opens


# ---------------------------------------------------------------------------
# Stage 3: reconcile FIFO-reconstructed opens against live IBKR positions
# ---------------------------------------------------------------------------
MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_expiry(contract_description):
    m = re.search(r"([A-Za-z]{3})(\d{1,2})'(\d{2})", contract_description)
    if not m:
        return None
    mon, day, yr = m.groups()
    try:
        return datetime(2000 + int(yr), MONTHS[mon], int(day)).date()
    except (KeyError, ValueError):
        return None


def enrich_open_positions(raw_positions, fifo_unmatched_opens, today=None):
    """
    raw_positions: from get_account_positions (LIVE, authoritative for what's
        actually open and at what real cost basis).
    fifo_unmatched_opens: from fifo_pair_trades() -- used ONLY to fill in
        entry_time (approximate) where a live position happens to match by
        symbol. If no match, entry_time is None and the position is flagged
        unverified_entry_date=True -- do not guess.
    """
    today = today or datetime.now(timezone.utc).date()
    fifo_by_sym = {o["sym"]: o for o in fifo_unmatched_opens}

    enriched = []
    for p in raw_positions:
        if p.get("position", 0) == 0:
            continue  # closed live even if FIFO thought it was still open
        sym = p["contract_description"].split()[0]
        match = fifo_by_sym.get(sym)
        exp = parse_expiry(p["contract_description"])
        dte = (exp - today).days if exp else None
        enriched.append(
            {
                "id": f"open_{p['contract_id']}",
                "sym": sym,
                "desc": p["contract_description"].replace(sym + " ", ""),
                "market_value": round(p["market_value"], 2),
                "cost_basis": round(p["average_price"] * 100 * p["position"], 2),
                "unrealized_pnl": round(p["unrealized_pnl"], 2),
                "daily_pnl": round(p.get("daily_pnl", 0), 2),
                "entry_time": match["entry_time"] if match else None,
                "entry_px": round(p["average_price"], 4),  # authoritative, includes commission
                "dte": dte,
                "unverified_entry_date": match is None,
            }
        )
    return enriched


# ---------------------------------------------------------------------------
# Stage 4: derived analytics fields
# ---------------------------------------------------------------------------
def compute_derived_fields(round_trips):
    chrono = sorted(round_trips, key=lambda x: x["exit_time"] or x["entry_time"] or "")
    gross_win = 0.0
    gross_loss = 0.0
    for t in chrono:
        if t["pnl"] > 0:
            gross_win += t["pnl"]
        elif t["pnl"] < 0:
            gross_loss += abs(t["pnl"])
        t["cum_pf"] = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

        if t["entry_time"] and t["exit_time"]:
            d1 = datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00"))
            d2 = datetime.fromisoformat(t["exit_time"].replace("Z", "+00:00"))
            t["days_held"] = round((d2 - d1).total_seconds() / 86400, 1)
        else:
            t["days_held"] = None

        cost_basis = round((t["entry_px"] or 0) * 100 * t["size"], 2) if t["entry_px"] else None
        t["cost_basis"] = cost_basis
        t["return_pct"] = round(t["pnl"] / cost_basis * 100, 1) if cost_basis else None

    return sorted(round_trips, key=lambda x: x["exit_time"] or x["entry_time"] or "", reverse=True)


# ---------------------------------------------------------------------------
# Convenience: run the full pipeline in one call
# ---------------------------------------------------------------------------
def run_pipeline(raw_trades, raw_positions):
    """
    Returns (closed_trades, open_positions):
      closed_trades  -- list of round-trip dicts, newest first, with all
                         derived fields (days_held, return_pct, cum_pf).
                         Each has a stable `id` suitable for keying journal
                         entries (strategy, thoughts, planned stop/target)
                         in external storage.
      open_positions -- list of live open positions enriched with FIFO-matched
                         entry_time where available, using LIVE cost-basis
                         price (average_price) as the authoritative entry price.
    """
    merged = merge_fills_by_order(raw_trades)
    round_trips, unmatched_opens = fifo_pair_trades(merged)
    round_trips = compute_derived_fields(round_trips)
    open_positions = enrich_open_positions(raw_positions, unmatched_opens)
    return round_trips, open_positions


# TODO (not yet implemented): capture contract_description (strike/expiry)
# from raw_positions at the moment a position closes, so future closed trades
# retain contract detail that historical trade-history data cannot provide.
# Requires diffing live positions between consecutive daily syncs to detect
# a close event, then attaching the last-known contract_description to the
# resulting round-trip record once it appears in trade history.

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 3:
        print("Usage: python trade_pipeline.py <raw_trades.json> <raw_positions.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        trades_in = json.load(f)
    with open(sys.argv[2]) as f:
        positions_in = json.load(f)

    closed, opened = run_pipeline(trades_in, positions_in)
    print(json.dumps({"closed_trades": closed, "open_positions": opened}, indent=2))
