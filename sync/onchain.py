"""
Deterministic onchain sync -> Supabase (no LLM, no wallet keys, read-only).

Pulls a wallet's crypto activity from the Zerion API and writes three tables:
  onchain_holdings  -- current token positions (+ our FIFO cost basis)
  onchain_trades    -- round-trip trades, FIFO-matched from swap history
  onchain_summary   -- one row: portfolio value, dry-powder split, 1y P&L, counts

Swap accounting:
  stable/native -> TOKEN     opens a lot of TOKEN  (cost = USD spent)
  TOKEN -> stable/native     closes TOKEN lots FIFO (proceeds = USD received)
  TOKEN_A -> TOKEN_B         closes A and opens B, both at the swap's USD value
  partial sells leave the remainder as an open lot -> a current holding
LP / staking / bridging / airdrops are not swaps -> ignored; any close we can't
back with an open lot is counted in `unmatched_activity`.

Env:
  ZERION_API_KEY       zk_prod_... (read-only portfolio data; used as HTTP Basic user)
  ONCHAIN_WALLET       0x... (default: the address below)
  ONCHAIN_CHAINS       comma list of Zerion chain ids (default: ethereum,binance-smart-chain)
  SUPABASE_URL, SUPABASE_SECRET_KEY
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from sync import Supabase, log, die  # reuse the PostgREST helper + logging

ZERION_BASE = "https://api.zerion.io/v1"
DEFAULT_WALLET = "0x3414ec2d1c63008e1cda0e2155b7334c446a0025"
DEFAULT_CHAINS = "ethereum,binance-smart-chain"
HTTP_TIMEOUT = 45


class TransientError(RuntimeError):
    """Rate limit / API outage / empty response -- expected, not a bug. main()
    exits 0 on these so a scheduled run doesn't page us; the tables are left
    untouched and the next run recovers."""

STABLES = {
    "USDC", "USDT", "DAI", "USDE", "USDS", "FRAX", "TUSD", "USDP", "GUSD",
    "PYUSD", "FDUSD", "USDD", "CRVUSD", "LUSD", "SUSD", "USDBC", "BUSD",
    "BSC-USD", "USDC.E", "USDT.E", "USDB", "GHO", "USD1",
}
# Cash-like legs: used to price a swap and to tell "buy" from "sell", but never
# turned into round-trip trades of their own. This wallet routes constantly
# through WETH/WBNB, so treating every hop as a taxable WETH event produces
# nonsense P&L. Instead WETH/WBNB carry a running inventory (TRACK_INV) so the
# *current* holding still gets a real weighted-average cost basis.
NATIVE = {"ETH", "BNB", "WETH", "WBNB"}
TRACK_INV = {"WETH", "WBNB"}

# Tokens with deep, reliably-oracled prices. Only swaps where BOTH legs are
# "trusted" (stable / native / blue-chip / Zerion-verified) give a clean read on
# execution spread -- everything else is polluted by thin-token price noise.
BLUECHIP = {
    "WBTC", "BTCB", "CBBTC", "TBTC", "SOL", "WSOL", "MSOL", "JITOSOL",
    "MATIC", "POL", "AVAX", "WAVAX", "LINK", "UNI", "AAVE", "LDO", "MKR",
    "ARB", "OP", "CRV", "PENDLE", "ENA", "MORPHO", "ONDO", "ADA", "XRP",
    "DOGE", "SHIB", "PEPE", "TRX", "DOT", "ATOM", "NEAR", "LTC", "BCH",
    "CAKE", "GALA",
}


def _trusted(leg: dict) -> bool:
    s = (leg.get("symbol") or "").upper()
    return is_cashlike(s) or s in BLUECHIP or bool(leg.get("verified"))


def _hdr():
    key = os.environ.get("ZERION_API_KEY") or die("ZERION_API_KEY not set")
    token = base64.b64encode(f"{key}:".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


def zget(path: str, params: dict | None = None) -> dict:
    """GET with retry. Raises (never returns empty) so a rate-limit / outage is a
    clean failure the caller can die on rather than silently empty data."""
    url = path if path.startswith("http") else f"{ZERION_BASE}{path}"
    last = None
    for attempt in (1, 2, 3, 4, 5):
        try:
            r = requests.get(url, headers=_hdr(), params=params, timeout=HTTP_TIMEOUT)
            if r.status_code == 429:
                last = "429 rate limited"
                time.sleep(5 * attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = str(e)
            if attempt == 5:
                break
            log(f"  zerion {path} attempt {attempt} failed ({e}); retrying")
            time.sleep(2 * attempt)
    raise TransientError(f"Zerion API unreachable for {path} ({last})")


def zpaged(path: str, params: dict) -> list:
    """Follow links.next until exhausted."""
    out, page = [], zget(path, params)
    while True:
        out.extend(page.get("data", []))
        nxt = (page.get("links") or {}).get("next")
        if not nxt or not page.get("data"):
            break
        page = zget(nxt)
    return out


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------
def _fung(attr: dict) -> dict:
    fi = attr.get("fungible_info") or {}
    impls = fi.get("implementations") or []
    impl = impls[0] if impls else {}
    return {
        "symbol": (fi.get("symbol") or "").upper() or None,
        "name": fi.get("name"),
        "address": (impl.get("address") or "").lower() or None,
        "chain": impl.get("chain_id"),
        "verified": bool((fi.get("flags") or {}).get("verified")),
    }


def _chain_of(item: dict) -> str | None:
    rel = (item.get("relationships") or {}).get("chain") or {}
    return ((rel.get("data") or {}).get("id"))


def is_stable(sym: str | None) -> bool:
    return bool(sym) and sym.upper() in STABLES


def is_cashlike(sym: str | None) -> bool:
    return is_stable(sym) or (bool(sym) and sym.upper() in NATIVE)


# ---------------------------------------------------------------------------
# Zerion fetches
# ---------------------------------------------------------------------------
def fetch_positions(wallet: str, chains: str) -> list[dict]:
    raw = zpaged(f"/wallets/{wallet}/positions/", {
        "currency": "usd",
        "filter[position_types]": "wallet",
        "filter[chain_ids]": chains,
        "filter[trash]": "only_non_trash",
        "page[size]": 100,
        "sort": "value",
    })
    out = []
    for p in raw:
        a = p.get("attributes") or {}
        f = _fung(a)
        qty = (a.get("quantity") or {}).get("float")
        out.append({
            "chain": _chain_of(p) or f["chain"],
            "symbol": f["symbol"],
            "name": f["name"],
            "address": f["address"],
            "quantity": qty,
            "price": a.get("price"),
            "value_usd": a.get("value"),
            "is_trash": bool((a.get("flags") or {}).get("is_trash")),
        })
    return out


def fetch_trades(wallet: str, chains: str) -> list[dict]:
    """Swap transactions, oldest first, normalised to {sold, bought, ...}."""
    raw = zpaged(f"/wallets/{wallet}/transactions/", {
        "currency": "usd",
        "filter[operation_types]": "trade",
        "filter[chain_ids]": chains,
        "page[size]": 100,
    })
    trades = []
    for tx in raw:
        a = tx.get("attributes") or {}
        if a.get("operation_type") != "trade":
            continue
        mined = a.get("mined_at")  # unix seconds
        ts = (datetime.fromtimestamp(mined, timezone.utc).isoformat().replace("+00:00", "Z")
              if isinstance(mined, (int, float)) else a.get("mined_at"))
        sold, bought = [], []
        for tr in a.get("transfers") or []:
            f = _fung(tr)
            leg = {
                "symbol": f["symbol"], "name": f["name"], "address": f["address"],
                "chain": _chain_of(tx) or f["chain"],
                "qty": abs(float(tr.get("quantity", {}).get("float") or 0) or 0),
                "usd": tr.get("value"),
                "price": tr.get("price"),
                "verified": f["verified"],
            }
            if tr.get("direction") == "out":
                sold.append(leg)
            elif tr.get("direction") == "in":
                bought.append(leg)
        if not sold or not bought:
            continue
        fee = a.get("fee")
        fee_usd = (fee.get("value") if isinstance(fee, dict)
                   else fee if isinstance(fee, (int, float)) else None)
        trades.append({
            "hash": a.get("hash"),
            "time": ts,
            "chain": _chain_of(tx),
            "sold": sold,
            "bought": bought,
            "fee_usd": fee_usd,
        })
    trades.sort(key=lambda t: t["time"] or "")
    return trades


def fetch_pnl(wallet: str, chains: str) -> dict:
    try:
        d = zget(f"/wallets/{wallet}/pnl/", {"currency": "usd", "filter[chain_ids]": chains})
        return (d.get("data") or {}).get("attributes") or {}
    except requests.RequestException as e:
        log(f"  (pnl endpoint unavailable: {e})")
        return {}


def fetch_year_chart(wallet: str, chains: str) -> dict:
    try:
        d = zget(f"/wallets/{wallet}/charts/year", {"currency": "usd", "filter[chain_ids]": chains})
        return (d.get("data") or {}).get("attributes") or {}
    except requests.RequestException as e:
        log(f"  (chart endpoint unavailable: {e})")
        return {}


# ---------------------------------------------------------------------------
# FIFO trade matching
# ---------------------------------------------------------------------------
def _key(chain: str | None, addr: str | None, sym: str | None) -> str:
    return f"{chain or '?'}:{addr or (sym or '?').lower()}"


def _leg_value(leg: dict) -> float | None:
    v = leg.get("usd")
    if v is None and leg.get("price") and leg.get("qty"):
        v = leg["price"] * leg["qty"]
    return v


def match_trades(swaps: list[dict]):
    """
    Returns (trade_rows, open_lots, unmatched_count).
    open_lots: { key: [ {qty, cost_usd, entry_time, entry_tx, entry_kind, symbol, name, chain, address} ] }
    """
    lots: dict[str, list] = {}
    inv: dict[str, list] = {}          # WETH/WBNB running inventory (cost basis only)
    rows: list[dict] = []
    unmatched = 0

    def inv_add(leg, usd, ts):
        if not leg["qty"] or leg["qty"] <= 0:
            return
        k = _key(leg["chain"], leg["address"], leg["symbol"])
        inv.setdefault(k, []).append({
            "qty": leg["qty"], "cost_usd": usd or 0.0, "entry_time": ts,
            "symbol": leg["symbol"], "name": leg["name"],
            "chain": leg["chain"], "address": leg["address"],
        })

    def inv_take(leg):
        k = _key(leg["chain"], leg["address"], leg["symbol"])
        q = inv.get(k) or []
        left = leg["qty"]
        while left > 1e-18 and q:
            lot = q[0]
            take = min(left, lot["qty"])
            frac = take / lot["qty"] if lot["qty"] else 0
            lot["cost_usd"] -= lot["cost_usd"] * frac
            lot["qty"] -= take
            left -= take
            if lot["qty"] <= 1e-18:
                q.pop(0)

    def open_lot(leg, cost_usd, ts, tx, kind):
        if not leg["qty"] or leg["qty"] <= 0:
            return
        k = _key(leg["chain"], leg["address"], leg["symbol"])
        lots.setdefault(k, []).append({
            "qty": leg["qty"], "cost_usd": cost_usd or 0.0,
            "entry_time": ts, "entry_tx": tx, "entry_kind": kind,
            "symbol": leg["symbol"], "name": leg["name"],
            "chain": leg["chain"], "address": leg["address"],
        })

    def close_lot(leg, proceeds_usd, ts, tx, kind):
        nonlocal unmatched
        k = _key(leg["chain"], leg["address"], leg["symbol"])
        queue = lots.get(k) or []
        qty_left = leg["qty"]
        proceeds_rate = (proceeds_usd / leg["qty"]) if (proceeds_usd and leg["qty"]) else 0.0
        while qty_left > 1e-18 and queue:
            lot = queue[0]
            take = min(qty_left, lot["qty"])
            frac = take / lot["qty"] if lot["qty"] else 0
            cost_part = lot["cost_usd"] * frac
            proc_part = proceeds_rate * take
            entry_px = (lot["cost_usd"] / lot["qty"]) if lot["qty"] else None
            exit_px = proceeds_rate or None
            hold_days = _days(lot["entry_time"], ts)
            rows.append({
                "chain": lot["chain"], "symbol": lot["symbol"], "name": lot["name"],
                "token_address": lot["address"],
                "entry_time": lot["entry_time"], "exit_time": ts,
                "entry_price": entry_px, "exit_price": exit_px,
                "qty": take, "cost_usd": cost_part, "proceeds_usd": proc_part,
                "realized_pnl_usd": proc_part - cost_part,
                "return_pct": ((proc_part - cost_part) / cost_part * 100) if cost_part else None,
                "hold_days": hold_days,
                "entry_tx": lot["entry_tx"], "exit_tx": tx,
                "entry_kind": lot["entry_kind"], "exit_kind": kind,
                "partial": frac < 0.999 or take < leg["qty"] - 1e-12,
            })
            lot["qty"] -= take
            lot["cost_usd"] -= cost_part
            qty_left -= take
            if lot["qty"] <= 1e-18:
                queue.pop(0)
        if qty_left > 1e-9:
            unmatched += 1  # sold more than we ever saw bought (transfer-in / airdrop origin)

    for s in swaps:
        ts, tx = s["time"], s["hash"]
        sold_cash = all(is_cashlike(x["symbol"]) for x in s["sold"])
        bought_cash = all(is_cashlike(x["symbol"]) for x in s["bought"])
        sold_val = sum(_leg_value(x) or 0 for x in s["sold"]) or None
        bought_val = sum(_leg_value(x) or 0 for x in s["bought"]) or None
        swap_val = sold_val or bought_val

        # keep WETH/WBNB inventory current (no realized rows)
        for b in s["bought"]:
            if b["symbol"] in TRACK_INV:
                inv_add(b, _leg_value(b) or swap_val, ts)
        for so in s["sold"]:
            if so["symbol"] in TRACK_INV:
                inv_take(so)

        if sold_cash and not bought_cash:
            # cash -> token(s): open each bought leg, cost split by leg value
            for b in s["bought"]:
                if is_cashlike(b["symbol"]):
                    continue
                cost = _leg_value(b) or (swap_val if len(s["bought"]) == 1 else None)
                open_lot(b, cost, ts, tx, "stable->token" if all(is_stable(x["symbol"]) for x in s["sold"]) else "native->token")
        elif bought_cash and not sold_cash:
            # token(s) -> cash: close each sold leg
            for so in s["sold"]:
                if is_cashlike(so["symbol"]):
                    continue
                proceeds = _leg_value(so) or (swap_val if len(s["sold"]) == 1 else None)
                close_lot(so, proceeds, ts, tx, "token->stable" if all(is_stable(x["symbol"]) for x in s["bought"]) else "token->native")
        elif not sold_cash and not bought_cash:
            # token -> token: close sold, open bought, at the swap USD value
            for so in s["sold"]:
                if is_cashlike(so["symbol"]):
                    continue
                close_lot(so, _leg_value(so) or swap_val, ts, tx, "token->token")
            for b in s["bought"]:
                if is_cashlike(b["symbol"]):
                    continue
                open_lot(b, _leg_value(b) or swap_val, ts, tx, "token->token")
        # cash<->cash (stable rotations) -> ignore

    return rows, lots, unmatched, inv


def _days(a_iso: str | None, b_iso: str | None):
    try:
        a = datetime.fromisoformat((a_iso or "").replace("Z", "+00:00"))
        b = datetime.fromisoformat((b_iso or "").replace("Z", "+00:00"))
        return round((b - a).total_seconds() / 86400, 1)
    except (TypeError, ValueError):
        return None


def _tid(r: dict, seq: int) -> str:
    # seq disambiguates FIFO slices that would otherwise hash identically
    # (e.g. one exit consuming two equal-size lots of the same token).
    raw = (f"{r['entry_tx']}|{r['exit_tx']}|{r.get('token_address')}|"
           f"{r['entry_time']}|{r['qty']:.10f}|{seq}")
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
def build(wallet: str, chains: str, now_iso: str):
    log("fetching Zerion positions / trades / pnl / chart")
    positions = fetch_positions(wallet, chains)
    swaps = fetch_trades(wallet, chains)
    pnl = fetch_pnl(wallet, chains)
    chart = fetch_year_chart(wallet, chains)
    log(f"  {len(positions)} positions, {len(swaps)} swap txns")

    if not swaps:
        raise TransientError("Zerion returned 0 trade transactions -- transient "
                             "API failure (this wallet has a long swap history)")

    trade_rows, open_lots, unmatched, weth_inv = match_trades(swaps)

    # drop FIFO dust slices: negligible cost AND negligible realized P&L
    DUST = 1.0
    trade_rows = [r for r in trade_rows
                  if (r["cost_usd"] or 0) >= DUST or abs(r["realized_pnl_usd"] or 0) >= DUST]

    # WETH/WBNB also leave the wallet via ops we don't fetch (unwrap, bridge,
    # transfer), so the trade-derived inventory over-counts. Trim each to the
    # live on-chain balance, keeping the MOST RECENT lots (FIFO throughout: the
    # earliest WETH was spent first, by trades and by everything else) -> the
    # cost basis is that of the stake actually still held.
    pos_qty = {_key(p["chain"], p["address"], p["symbol"]): (p["quantity"] or 0.0)
               for p in positions}
    for k, q in list(weth_inv.items()):
        target = pos_qty.get(k, 0.0)
        q.sort(key=lambda l: l["entry_time"] or "", reverse=True)   # newest first
        kept, acc = [], 0.0
        for lot in q:
            if acc >= target - 1e-12:
                break
            take = min(lot["qty"], target - acc)
            frac = take / lot["qty"] if lot["qty"] else 0
            kept.append({**lot, "qty": take, "cost_usd": lot["cost_usd"] * frac})
            acc += take
        weth_inv[k] = kept

    # ---- holdings: Zerion live position is truth for value; our lots give cost basis
    lot_by_key = {}
    for k, q in list(open_lots.items()) + list(weth_inv.items()):
        tot_q = sum(l["qty"] for l in q)
        tot_c = sum(l["cost_usd"] for l in q)
        first = min((l["entry_time"] for l in q if l["entry_time"]), default=None)
        if tot_q > 1e-15:
            lot_by_key[k] = {"qty": tot_q, "cost": tot_c, "first": first}

    # portfolio totals count every position (Zerion's non-trash set); the table
    # only lists positions currently worth >= MIN_HOLDING.
    MIN_HOLDING = 3.0
    holdings, coins_val, stables_val = [], 0.0, 0.0
    for p in positions:
        sym = p["symbol"]
        k = _key(p["chain"], p["address"], sym)
        val = p["value_usd"] or 0.0
        stable = is_stable(sym)
        lot = lot_by_key.get(k)
        cost = lot["cost"] if lot else None
        avg_entry = (lot["cost"] / lot["qty"]) if (lot and lot["qty"]) else None
        unreal = (val - cost) if (cost is not None and not stable) else None
        if stable:
            stables_val += val
        else:
            coins_val += val
        if val < MIN_HOLDING:
            continue
        holdings.append({
            "id": k, "wallet": wallet, "chain": p["chain"], "symbol": sym,
            "name": p["name"], "token_address": p["address"],
            "quantity": p["quantity"], "price": p["price"], "value_usd": val,
            "cost_basis_usd": cost, "avg_entry_price": avg_entry,
            "unrealized_pnl_usd": unreal,
            "unrealized_pnl_pct": (unreal / cost * 100) if (unreal is not None and cost) else None,
            "first_bought_at": lot["first"] if lot else None,
            "is_stablecoin": stable, "is_spam": p["is_trash"], "synced_at": now_iso,
        })

    trades, seen = [], set()
    for i, r in enumerate(trade_rows):
        tid = _tid(r, i)
        while tid in seen:                       # last-resort guard
            i += 1_000_000
            tid = _tid(r, i)
        seen.add(tid)
        trades.append({**r, "id": tid, "wallet": wallet, "synced_at": now_iso})

    # ---- summary
    portfolio_value = coins_val + stables_val
    pts = chart.get("points") or []
    win_usd = win_start = win_end = win_days = None
    if len(pts) >= 2:
        win_usd = round((pts[-1][1] or 0) - (pts[0][1] or 0), 2)
        win_start = datetime.fromtimestamp(pts[0][0], timezone.utc).date().isoformat()
        win_end = datetime.fromtimestamp(pts[-1][0], timezone.utc).date().isoformat()
        win_days = round((pts[-1][0] - pts[0][0]) / 86400)
    realized_matched = round(sum(t["realized_pnl_usd"] or 0 for t in trades), 2)

    def _bands(rows):
        p = [t["realized_pnl_usd"] or 0 for t in rows]
        return (sum(1 for x in p if x >= 200), sum(1 for x in p if x <= -200),
                sum(1 for x in p if 0 < x < 50), sum(1 for x in p if -50 < x < 0))

    big_w, big_l, small_w, small_l = _bands(trades)

    _cut1y = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat().replace("+00:00", "Z")
    _t1y = [t for t in trades if (t.get("exit_time") or "") >= _cut1y]
    trade_count_1y = len(_t1y)
    realized_1y = round(sum(t["realized_pnl_usd"] or 0 for t in _t1y), 2)
    big_w_1y, big_l_1y, small_w_1y, small_l_1y = _bands(_t1y)

    cutoff_1y = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat().replace("+00:00", "Z")
    gas_fees = round(sum(s.get("fee_usd") or 0 for s in swaps), 2)
    gas_fees_1y = round(sum(s.get("fee_usd") or 0 for s in swaps
                            if (s.get("time") or "") >= cutoff_1y), 2)

    # --- execution spread (bid/ask + slippage + LP fee), de-noised ---------
    # per swap: value given up minus value received. Zerion prices thin tokens
    # unreliably, so: (1) clamp each swap to a plausible band, (2) trust only
    # swaps where both legs are well-priced, measure the spread rate there, and
    # apply that rate to total volume.
    f_raw = f_clamped = f_clamped_1y = clean_fric = clean_vol = all_vol = 0.0
    n_clamped = n_clean = n_priced = 0
    LO, HI = -0.03, 0.12                     # a swap can't really cost <-3% or >12%
    for s in swaps:
        sv = sum(_leg_value(x) or 0 for x in s["sold"])
        bv = sum(_leg_value(x) or 0 for x in s["bought"])
        if not (sv and bv):
            continue
        n_priced += 1
        vol = (sv + bv) / 2
        all_vol += vol
        d = sv - bv
        f_raw += d
        dc = max(LO * vol, min(HI * vol, d))
        if dc != d:
            n_clamped += 1
        f_clamped += dc
        if (s.get("time") or "") >= cutoff_1y:
            f_clamped_1y += dc
        if all(_trusted(x) for x in s["sold"] + s["bought"]):
            n_clean += 1
            clean_fric += dc
            clean_vol += vol
    clean_rate = (clean_fric / clean_vol) if clean_vol else 0.0
    friction = round(f_clamped, 2)          # headline: measured, per-swap outliers capped
    friction_1y = round(f_clamped_1y, 2)
    rate_est = round(clean_rate * all_vol, 2)
    log(f"  spread: raw ${f_raw:,.0f} | clamped ${f_clamped:,.0f} ({n_clamped} capped) | "
        f"clean {n_clean}/{n_priced} swaps rate {clean_rate*100:.2f}% -> ${rate_est:,.0f} "
        f"on ${all_vol:,.0f} volume")

    summary = {
        "id": "current", "wallet": wallet,
        "portfolio_value_usd": round(portfolio_value, 2),
        "value_in_coins_usd": round(coins_val, 2),
        "value_in_stables_usd": round(stables_val, 2),
        "pnl_window_days": win_days, "pnl_window_start": win_start,
        "pnl_window_end": win_end, "pnl_window_usd": win_usd,
        "realized_matched_usd": realized_matched,
        "realized_pnl_all_usd": pnl.get("realized_gain"),   # Zerion all-in (gas/native/unattributed); may be None
        "unrealized_pnl_usd": pnl.get("unrealized_gain",
                                      round(sum(h["unrealized_pnl_usd"] or 0 for h in holdings), 2)),
        "trade_count": len(trades),
        "open_position_count": sum(1 for h in holdings
                                   if not h["is_stablecoin"] and not h["is_spam"]
                                   and (h["value_usd"] or 0) >= 1),
        "unmatched_activity": unmatched,
        "gas_fees_usd": gas_fees,
        "swap_friction_usd": friction,
        "swap_friction_raw_usd": round(f_raw, 2),
        "swap_volume_usd": round(all_vol, 2),
        "swap_spread_rate": round(clean_rate, 5),
        "big_winners_count": big_w,
        "big_losers_count": big_l,
        "small_winners_count": small_w,
        "small_losers_count": small_l,
        "trade_count_1y": trade_count_1y,
        "realized_1y_usd": realized_1y,
        "gas_fees_1y_usd": gas_fees_1y,
        "swap_friction_1y_usd": friction_1y,
        "big_winners_1y": big_w_1y,
        "big_losers_1y": big_l_1y,
        "small_winners_1y": small_w_1y,
        "small_losers_1y": small_l_1y,
        "synced_at": now_iso,
    }
    return holdings, trades, summary


def main() -> None:
    wallet = (os.environ.get("ONCHAIN_WALLET") or DEFAULT_WALLET).lower()
    chains = os.environ.get("ONCHAIN_CHAINS") or DEFAULT_CHAINS
    supabase_url = os.environ.get("SUPABASE_URL") or die("SUPABASE_URL not set")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY") or die("SUPABASE_SECRET_KEY not set")
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        holdings, trades, summary = build(wallet, chains, now_iso)
    except TransientError as e:
        log(f"transient: {e}. Tables left intact; next run will recover. Exiting 0.")
        return
    log(f"built {len(holdings)} holdings, {len(trades)} round-trip trades, "
        f"{summary['unmatched_activity']} unmatched swaps")

    if "--dry-run" in sys.argv:
        import json
        print(json.dumps({"summary": summary, "holdings": holdings[:5], "trades": trades[:5]},
                         indent=2, default=str))
        return

    if not holdings and not trades:
        die("Zerion returned no positions and no trades -- refusing to wipe the "
            "onchain tables. Check ZERION_API_KEY / wallet / chain ids.")

    sb = Supabase(supabase_url, secret_key)

    # Zerion's /transactions/ endpoint has been seen to return a materially
    # truncated page set on a 200 OK -- no error, no rate-limit, just fewer
    # swaps than a run an hour earlier (2026-09-01: 1854 -> 1108 swap txns,
    # 1269 -> 724 round-trips, silently dropping ~18mo of history and
    # flipping realized P&L from a real loss to a fake $33.5k gain). zget's
    # retry logic can't catch this since nothing errors. Guard on the
    # symptom instead: a healthy run only adds trades day to day, so a big
    # drop vs the last successful sync means this run's data is suspect.
    prev = sb.get("onchain_summary", "trade_count")
    prev_count = (prev[0].get("trade_count") if prev else None) or 0
    if prev_count and len(trades) < prev_count * 0.85:
        log(f"trade count dropped {prev_count} -> {len(trades)} (>15%) -- Zerion likely "
            f"returned a truncated transaction history. Tables left intact; next run "
            f"will recover. Exiting 0.")
        return

    wrote = []
    if holdings:
        sb.upsert("onchain_holdings", holdings); wrote.append("onchain_holdings")
    if trades:
        sb.upsert("onchain_trades", trades); wrote.append("onchain_trades")
    sb.upsert("onchain_summary", [summary])

    # drop anything this run didn't refresh -- but only on tables we actually
    # wrote to this run (never let an empty fetch wipe a populated table).
    for table in wrote:
        r = requests.delete(f"{sb.base}/{table}", params={"synced_at": f"lt.{now_iso}"},
                            headers={**sb.headers, "Prefer": "return=representation"},
                            timeout=HTTP_TIMEOUT)
        if r.ok:
            try:
                n = len(r.json())
            except ValueError:
                n = 0
            if n:
                log(f"  {table}: removed {n} stale rows")
    log("onchain sync done")


if __name__ == "__main__":
    main()
