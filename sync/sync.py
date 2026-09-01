"""
Deterministic IBKR -> Supabase sync.

Flow (no LLM, no interactive auth):
  1. Pull an Activity Flex Query from IBKR's Flex Web Service using a token.
  2. Parse the XML into the raw_trades / raw_positions dict shapes that
     trade_pipeline.py expects.
  3. run_pipeline() -> (closed_trades, open_positions).
  4. Enrich closed trades: contract detail (from Flex), a chronological
     trade_seq, and underlying stock prices at entry / exit / now (Yahoo
     Finance chart API -- keyless, one call per ticker).
  5. Upsert closed_trades (dedup on stable id; never touches the user's
     journal columns) and reconcile open_positions (upsert current, delete
     stale), plus account_summary, in Supabase via the secret key.

Env vars (all required except FLEX_QUERY_ID / FLEX_TIMEZONE):
  FLEX_TOKEN            IBKR Flex Web Service token
  FLEX_QUERY_ID         Activity Flex Query id (default: 1618686 = "TradeJournalSync")
  FLEX_TIMEZONE         tz for IBKR trade timestamps (default: America/New_York)
  SUPABASE_URL          https://<ref>.supabase.co
  SUPABASE_SECRET_KEY   sb_secret_... (server-only, bypasses RLS)

Exit code is non-zero on any failure so a scheduled run fails visibly.
"""

from __future__ import annotations

import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None

import requests

from trade_pipeline import run_pipeline

FLEX_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
FLEX_V = "3"
YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
USER_AGENT = "Mozilla/5.0 (compatible; trade-journal-sync/1.0)"
HTTP_TIMEOUT = 60

MONTHS_ABBR = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Columns the sync job owns on closed_trades. The user-editable journal columns
# (strategy, journal_thoughts, planned_stop, planned_target) are deliberately
# absent so an upsert never overwrites what the user typed in the dashboard.
CLOSED_SYNC_COLUMNS = [
    "id", "symbol", "entry_time", "entry_price", "exit_time", "exit_price",
    "size", "pnl", "commission", "cost_basis", "return_pct", "days_held",
    "ambiguous", "ambiguous_reason", "entry_order_id", "exit_order_id",
    "contract_description", "contract_expiry", "contract_type", "contract_strike",
    "underlying_price_entry", "underlying_price_exit", "underlying_price_latest",
    "underlying_price_latest_at", "underlying_price_horizon", "contract_horizon_date",
    "underlying_price_peak", "underlying_peak_date", "vix_at_entry", "vix_at_exit",
    "price_window", "setup_entry", "chart_read_entry", "chart_read_exit",
    "setup_structure", "setup_score", "setup_reasons", "setup_criteria",
    "trade_grade", "grade_points", "grade_reasons",
    "trade_seq", "synced_at",
]


def log(msg: str) -> None:
    print(f"[sync] {msg}", flush=True)


def die(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"[sync] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# IBKR Flex Web Service
# ---------------------------------------------------------------------------
def _flex_get(path: str, params: dict) -> ET.Element:
    resp = requests.get(
        f"{FLEX_BASE}/{path}",
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError as e:
        die(f"Flex {path}: response was not XML ({e}). First 300 chars:\n{resp.text[:300]}")


def fetch_flex_statement(token: str, query_id: str, max_wait: int = 180) -> ET.Element:
    """Returns the <FlexQueryResponse> root element."""
    root = _flex_get("SendRequest", {"t": token, "q": query_id, "v": FLEX_V})
    status = (root.findtext("Status") or "").strip()
    if status != "Success":
        code = root.findtext("ErrorCode") or "?"
        message = root.findtext("ErrorMessage") or "unknown error"
        die(f"Flex SendRequest failed ({status}, code {code}): {message}")

    reference_code = root.findtext("ReferenceCode").strip()
    get_url = (root.findtext("Url") or f"{FLEX_BASE}/GetStatement").strip()
    log(f"Flex request accepted, reference code {reference_code}; waiting for generation")

    deadline = time.time() + max_wait
    delay = 5
    while True:
        resp = requests.get(
            get_url,
            params={"t": token, "q": reference_code, "v": FLEX_V},
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        stmt = ET.fromstring(resp.content)

        if stmt.tag == "FlexQueryResponse":
            return stmt

        # Still generating (code 1019) or a hard error
        status = (stmt.findtext("Status") or "").strip()
        code = stmt.findtext("ErrorCode") or "?"
        message = stmt.findtext("ErrorMessage") or ""
        if status == "Warn" and code == "1019" and time.time() < deadline:
            log(f"  not ready yet (code {code}); retrying in {delay}s")
            time.sleep(delay)
            delay = min(delay + 5, 30)
            continue
        die(f"Flex GetStatement failed ({status}, code {code}): {message}")


# ---------------------------------------------------------------------------
# XML -> pipeline input shapes
# ---------------------------------------------------------------------------
def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_dt(raw: str, tz) -> str | None:
    """IBKR 'yyyyMMdd;HHmmss' (or 'yyyyMMdd') -> ISO8601 UTC string."""
    if not raw:
        return None
    raw = raw.strip().replace(" ", "")
    try:
        if ";" in raw:
            d, t = raw.split(";")
        elif len(raw) > 8:
            d, t = raw[:8], raw[8:]
        else:
            d, t = raw, "000000"
        t = (t + "000000")[:6]
        naive = datetime.strptime(d + t, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    aware = naive.replace(tzinfo=tz) if tz else naive.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def flex_to_raw_trades(root: ET.Element, tz) -> list[dict]:
    out = []
    for tr in root.iter("Trade"):
        a = tr.attrib
        if a.get("levelOfDetail", "EXECUTION") != "EXECUTION":
            continue
        side = (a.get("buySell") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        symbol = a.get("underlyingSymbol") or a.get("symbol") or ""
        order_id = a.get("ibOrderID") or a.get("orderID") or a.get("tradeID")
        out.append({
            "trade_id": a.get("tradeID"),
            "symbol": symbol,
            "sec_type": a.get("assetCategory"),
            "currency": a.get("currency"),
            "side": side,
            "size": abs(_to_float(a.get("quantity"))),
            "price": _to_float(a.get("tradePrice")),
            "trade_time": _parse_dt(a.get("dateTime") or a.get("tradeDate"), tz),
            "commission": abs(_to_float(a.get("ibCommission"))),
            "realized_pnl": _to_float(a.get("fifoPnlRealized")),
            "order_id": int(order_id) if order_id and str(order_id).isdigit() else order_id,
        })
    return out


def dte_from_expiry(expiry: str, today=None) -> int | None:
    if not expiry:
        return None
    try:
        exp = datetime.strptime(expiry.strip(), "%Y%m%d").date()
    except ValueError:
        return None
    today = today or datetime.now(timezone.utc).date()
    return (exp - today).days


def flex_to_raw_positions(root: ET.Element) -> list[dict]:
    out = []
    for pos in root.iter("OpenPosition"):
        a = pos.attrib
        if a.get("levelOfDetail", "SUMMARY") != "SUMMARY":
            continue
        qty = _to_float(a.get("position"))
        if qty == 0:
            continue
        oid = a.get("originatingOrderID") or a.get("originatingTransactionID")
        out.append({
            "contract_id": a.get("conid"),
            "contract_description": a.get("description") or a.get("symbol") or "",
            "position": qty,
            "market_price": _to_float(a.get("markPrice")),
            "market_value": _to_float(a.get("positionValue")),
            # costBasisPrice already includes commission (IBKR's authoritative entry price)
            "average_price": _to_float(a.get("costBasisPrice")),
            "unrealized_pnl": _to_float(a.get("fifoPnlUnrealized")),
            "daily_pnl": 0.0,  # not in an Activity Flex Query
            "currency": a.get("currency"),
            "entry_order_id": int(oid) if oid and str(oid).isdigit() else None,
            # contract detail + the position's own open date (authoritative when present)
            "put_call": (a.get("putCall") or "").strip().upper()[:1] or None,
            "strike": _to_float(a.get("strike"), None),
            "expiry": (a.get("expiry") or "").strip() or None,
            "open_datetime": a.get("openDateTime") or a.get("holdingPeriodDateTime"),
        })
    return out


def flex_position_contract_by_conid(raw_positions: list[dict], tz) -> dict:
    """conid -> {put_call, strike, expiry, open_time} from the parsed OpenPositions."""
    out = {}
    for p in raw_positions:
        if not p.get("contract_id"):
            continue
        out[p["contract_id"]] = {
            "put_call": p.get("put_call"),
            "strike": p.get("strike"),
            "expiry": p.get("expiry"),
            "open_time": _parse_dt(p.get("open_datetime"), tz),
        }
    return out


def flex_to_account_summary(root: ET.Element, now_iso: str, usdcad: float | None) -> dict | None:
    """
    Account-level balances in BASE currency, from the NAV + Cash Report sections.
      net_liquidation  <- latest EquitySummaryByReportDateInBase.total
      available_funds  <- CashReport BASE_SUMMARY endingSettledCash (fallback endingCash)
    Also derives net_liquidation_usd / available_funds_usd via `usdcad` so the
    dashboard can show everything in USD alongside the already-USD position
    figures, regardless of the account's base currency.
    Returns None if neither section is present (query not updated yet) -- the
    caller then leaves the existing account_summary row untouched.
    """
    nav_rows = [e.attrib for e in root.iter("EquitySummaryByReportDateInBase")]
    latest_nav = max(nav_rows, key=lambda a: a.get("reportDate", ""), default=None)

    cash_base = None
    for c in root.iter("CashReportCurrency"):
        if (c.attrib.get("currency") or "").upper() == "BASE_SUMMARY":
            cash_base = c.attrib
            break

    if latest_nav is None and cash_base is None:
        return None

    log(f"DEBUG nav row: {latest_nav}")
    log(f"DEBUG cash_base row: {cash_base}")
    for c in root.iter("CashReportCurrency"):
        log(f"DEBUG cash segment [{c.attrib.get('currency')}]: {c.attrib}")

    def _date(v):
        v = (v or "").strip()
        if len(v) == 8 and v.isdigit():
            return f"{v[:4]}-{v[4:6]}-{v[6:]}"
        return v or None

    row = {"id": "current", "synced_at": now_iso}
    if latest_nav is not None:
        row["net_liquidation"] = _to_float(latest_nav.get("total"), None)
        row["currency"] = latest_nav.get("currency")
        row["as_of"] = _date(latest_nav.get("reportDate"))
    if cash_base is not None:
        row["available_funds"] = _to_float(
            cash_base.get("endingSettledCash") or cash_base.get("endingCash"), None
        )
        row.setdefault("currency", cash_base.get("currency"))
        row.setdefault("as_of", _date(cash_base.get("toDate")))

    row["fx_usdcad"] = usdcad
    row["net_liquidation_usd"] = _to_usd(row.get("net_liquidation"), row.get("currency"), usdcad)
    row["available_funds_usd"] = _to_usd(row.get("available_funds"), row.get("currency"), usdcad)
    return row


# ---------------------------------------------------------------------------
# Enrichment: contract detail + underlying stock prices
# ---------------------------------------------------------------------------
def flex_contract_by_order(root: ET.Element) -> dict:
    """{ str(orderId): {expiry, putCall, strike} } from the Flex Trade rows."""
    out = {}
    for tr in root.iter("Trade"):
        a = tr.attrib
        oid = a.get("ibOrderID") or a.get("orderID") or a.get("tradeID")
        if not oid:
            continue
        out[str(oid)] = {
            "expiry": (a.get("expiry") or "").strip() or None,
            "putCall": (a.get("putCall") or "").strip().upper()[:1] or None,
            "strike": _to_float(a.get("strike"), None),
        }
    return out


def contract_desc(expiry: str | None, strike, put_call: str | None) -> str | None:
    """'20261120', 19.0, 'C' -> '20NOV26 19 C'."""
    parts = []
    if expiry and len(expiry) == 8 and expiry.isdigit():
        mon = MONTHS_ABBR[int(expiry[4:6]) - 1] if expiry[4:6].isdigit() else expiry[4:6]
        parts.append(f"{int(expiry[6:])}{mon}{expiry[2:4]}")
    if strike is not None:
        parts.append(f"{strike:g}")
    if put_call:
        parts.append(put_call)
    return " ".join(parts) or None


def _yf_chart(symbol: str, p1: int, p2: int) -> dict:
    """One Yahoo chart call. Returns {latest, latest_at, closes:{date:px}}; best-effort."""
    rec = {"latest": None, "latest_at": None, "closes": {}}
    for attempt in (1, 2):
        try:
            r = requests.get(
                YF_CHART + symbol.replace("^", "%5E"),
                params={"period1": p1, "period2": p2, "interval": "1d"},
                headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT,
            )
            if r.status_code == 429 and attempt == 1:
                time.sleep(3)
                continue
            r.raise_for_status()
            res = (r.json().get("chart", {}).get("result") or [None])[0]
            if not res:
                break
            meta = res.get("meta", {}) or {}
            rec["latest"] = meta.get("regularMarketPrice")
            if meta.get("regularMarketTime"):
                rec["latest_at"] = (datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)
                                    .isoformat().replace("+00:00", "Z"))
            ts = res.get("timestamp") or []
            closes = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
            for i, sec in enumerate(ts):
                c = closes[i] if i < len(closes) else None
                if c is not None:
                    d = datetime.fromtimestamp(sec, timezone.utc).date().isoformat()
                    rec["closes"][d] = round(float(c), 4)
            break
        except Exception as e:  # noqa: BLE001 - best-effort enrichment
            if attempt == 2:
                log(f"  yahoo {symbol}: {e}")
    return rec


def _period_bounds(since: "datetime.date"):
    p1 = int((datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
              - timedelta(days=7)).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    return p1, p2


def fetch_underlying_prices(tickers: set, since: "datetime.date") -> dict:
    """{ ticker: {latest, latest_at, closes} } -- one Yahoo call per ticker, best-effort."""
    p1, p2 = _period_bounds(since)
    out = {}
    for t in sorted(tickers):
        out[t] = _yf_chart(t, p1, p2)
        time.sleep(0.25)
    return out


def fetch_vix(since: "datetime.date") -> dict:
    """{ yyyy-mm-dd: VIX close } -- one Yahoo call for ^VIX."""
    p1, p2 = _period_bounds(since)
    return _yf_chart("^VIX", p1, p2).get("closes", {})


def fetch_usdcad_rate() -> float | None:
    """Latest USD/CAD rate (1 USD = N CAD) from Yahoo; None if the fetch fails --
    best-effort, never blocks the sync."""
    today = datetime.now(timezone.utc).date()
    p1, p2 = _period_bounds(today)
    return _yf_chart("CAD=X", p1, p2).get("latest")


def _to_usd(value: float | None, currency: str | None, usdcad: float | None) -> float | None:
    """Convert an account-currency figure to USD. Pass-through for USD, divide by
    the USD/CAD rate for CAD, null for anything else or a missing rate."""
    if value is None:
        return None
    cur = (currency or "").upper()
    if cur == "USD":
        return round(value, 2)
    if cur == "CAD" and usdcad:
        return round(value / usdcad, 2)
    return None


def close_on_or_before(closes: dict, iso_date: str | None) -> float | None:
    if not iso_date:
        return None
    d = datetime.strptime(iso_date[:10], "%Y-%m-%d").date()
    for _ in range(7):  # walk back over weekends / holidays
        hit = closes.get(d.isoformat())
        if hit is not None:
            return hit
        d -= timedelta(days=1)
    return None


def favorable_extreme(closes: dict, start_iso: str, end_iso: str, is_put: bool):
    """
    Most favourable daily close in [start, end] -- highest for a call, lowest for
    a put. Returns (price, date_iso), or (None, None) if the window is empty.
    """
    try:
        s = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
        e = datetime.strptime(end_iso[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, None
    best_p, best_d, d = None, None, s
    while d <= e:
        c = closes.get(d.isoformat())
        if c is not None and (best_p is None or (c < best_p if is_put else c > best_p)):
            best_p, best_d = c, d.isoformat()
        d += timedelta(days=1)
    return best_p, best_d


def horizon_date(expiry: str | None, exit_iso: str | None) -> "datetime.date":
    """
    The date the 'if I had held' comparison runs to: the contract's expiry, or
    today if it hasn't expired yet. Falls back to exit + 90 days when the Flex
    record has no expiry (most of this account's contracts are ~3 months).
    """
    today = datetime.now(timezone.utc).date()
    if expiry and len(expiry) == 8 and expiry.isdigit():
        exp = datetime.strptime(expiry, "%Y%m%d").date()
    elif exit_iso:
        exp = datetime.strptime(exit_iso[:10], "%Y-%m-%d").date() + timedelta(days=90)
    else:
        return today
    return min(exp, today)


# ---------------------------------------------------------------------------
# Chart window + trend read
# ---------------------------------------------------------------------------
CHART_WINDOW_DAYS = 45  # trading days shown before entry and after exit


def _sorted_closes(closes: dict):
    return sorted(closes.items())  # [(iso_date, px), ...] chronological


def price_window(closes: dict, entry_iso: str | None, exit_iso: str | None,
                 open_ended: bool = False) -> list:
    """
    [[date, close], ...] for ~CHART_WINDOW_DAYS trading days each side of the trade.
    open_ended=True (a still-open position): keep everything from entry to the
    latest bar instead of cutting off CHART_WINDOW_DAYS after entry.
    """
    if not closes or not entry_iso:
        return []
    rows = _sorted_closes(closes)
    dates = [d for d, _ in rows]
    def idx_on_or_after(iso):
        for i, d in enumerate(dates):
            if d >= iso[:10]:
                return i
        return len(dates) - 1
    i_in = idx_on_or_after(entry_iso)
    i_out = idx_on_or_after(exit_iso) if exit_iso else i_in
    lo = max(0, i_in - CHART_WINDOW_DAYS)
    hi = len(rows) if open_ended else min(len(rows), i_out + CHART_WINDOW_DAYS + 1)
    return [[d, c] for d, c in rows[lo:hi]]


def _sma(rows, i, k):
    seg = rows[max(0, i - k + 1):i + 1]
    return sum(c for _, c in seg) / len(seg) if seg else None


def trend_features(closes: dict, on_iso: str | None) -> dict | None:
    """Trend snapshot as of `on_iso` (or the closest prior trading day)."""
    if not closes or not on_iso:
        return None
    rows = _sorted_closes(closes)
    target = on_iso[:10]
    i = None
    for j, (d, _) in enumerate(rows):
        if d <= target:
            i = j
        else:
            break
    if i is None or i < 5:
        return None
    px = rows[i][1]
    sma20 = _sma(rows, i, 20)
    sma50 = _sma(rows, i, 50)
    sma10_now, sma10_prev = _sma(rows, i, 10), _sma(rows, max(0, i - 3), 10)
    look = rows[max(0, i - 63):i + 1]  # ~3 months
    lo3 = min(c for _, c in look)
    hi3 = max(c for _, c in look)
    ret5 = (px / rows[i - 5][1] - 1) * 100 if i >= 5 else None
    ret20 = (px / rows[i - 20][1] - 1) * 100 if i >= 20 else None
    return {
        "px": round(px, 2),
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "above20": sma20 is not None and px > sma20,
        "above50": sma50 is not None and px > sma50,
        "ma10_rising": sma10_now is not None and sma10_prev is not None and sma10_now > sma10_prev,
        "ret5": round(ret5, 1) if ret5 is not None else None,
        "ret20": round(ret20, 1) if ret20 is not None else None,
        "pct_of_range": round((px - lo3) / (hi3 - lo3) * 100) if hi3 > lo3 else 50,
        "pct_off_low": round((px / lo3 - 1) * 100),
        "crossed_up_20": (rows[max(0, i - 4)][1] < (_sma(rows, max(0, i - 4), 20) or 0)
                          and sma20 is not None and px > sma20),
    }


def entry_intent(f: dict | None, is_put: bool = False) -> str | None:
    """
    "reversal" / "continuation" / "chop" -- the kind of entry this is, read off
    the chart and framed in the trade's own direction (mirror-image for puts).
    Same logic as the Strategy-column buckets. A put shorting an overextended
    top is a *reversal*, exactly like a call bottom-fishing a washed-out low.
    """
    if not f:
        return None
    poR = f.get("pct_of_range", 50)
    poR = 100 - poR if is_put else poR                  # 0 = at the fade / bottom-fish extreme
    with_20 = (not f["above20"]) if is_put else f["above20"]
    with_50 = (not f["above50"]) if is_put else f["above50"]
    if poR <= 35 and not with_20:
        return "reversal"
    if poR >= 55 and with_20 and with_50:
        return "continuation"
    return "chop"


def _dir_stretch(f: dict, is_put: bool):
    """(20-day return, % vs 20-day avg) re-signed so + means 'moved the trade's way'."""
    r20 = f.get("ret20")
    move = (-r20 if r20 is not None else None) if is_put else r20
    raw = ((f["px"] / f["sma20"] - 1) * 100) if f.get("sma20") else 0.0
    return move, (-raw if is_put else raw)


def classify_setup(f: dict | None, is_put: bool = False) -> str | None:
    if not f:
        return None
    intent = entry_intent(f, is_put)
    move, stretch = _dir_stretch(f, is_put)
    move = move or 0
    if intent == "continuation":
        return "extended trend — chasing" if (move > 35 or stretch > 22) else "continuation — with trend"
    if intent == "reversal":
        turning = (f.get("crossed_up_20") or f.get("ma10_rising")) if not is_put else (not f["above20"])
        if turning:
            return "reversal — turn confirming"
        if move < -15 or stretch < -10:
            return "fade — overextended" if is_put else "bottom-fish — washed out"
        return "early reversal — no turn yet"
    return "range / no trend"


def _mmdd(iso):
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %-d")
    except (TypeError, ValueError):
        return iso[:10] if iso else "?"


def entry_read(sym, f, setup, strike, put_call, expiry_iso, entry_iso) -> str | None:
    if not f:
        return None
    parts = [f"{_mmdd(entry_iso)} — {sym} ${f['px']:.2f}."]
    if f.get("ret20") is not None:
        d = "up" if f["ret20"] >= 0 else "down"
        parts.append(f"{d.capitalize()} {abs(f['ret20'])}% over 20 days"
                     + (f" (+{f['pct_off_low']}% off the recent low)" if f["pct_off_low"] >= 8 else "") + ".")
    if f.get("crossed_up_20") and f["sma20"] is not None and put_call != "P":
        parts.append(f"Just reclaimed its 20-day average (${f['sma20']:.2f})"
                     + (", and back above its 50-day." if f["above50"]
                        else ", still below its 50-day."))
    elif f["sma20"] is not None:
        p = ("Above" if f["above20"] else "Below") + f" its 20-day (${f['sma20']:.2f})"
        if f["sma50"] is not None:
            p += ", " + ("above" if f["above50"] else "below") + " its 50-day"
        parts.append(p + ".")
    poR = f["pct_of_range"]
    parts.append(("Near the top of its 3-month range." if poR >= 70
                  else "Near the bottom of its 3-month range." if poR <= 30
                  else "Mid-range."))
    if setup:
        parts.append(f"Setup: {setup}.")
    if strike is not None and f["px"]:
        moneyness = (strike / f["px"] - 1) * 100
        side = "OTM" if (moneyness > 0) == (put_call != "P") else "ITM"
        dte = _days_between(entry_iso, expiry_iso)
        parts.append(f"Contract {abs(round(moneyness))}% {side}"
                     + (f", {dte} days to expiry." if dte else "."))
    return " ".join(parts)


def exit_read(sym, f_exit, up_days, held_n, after_rows, exit_iso, ifheld_pct, is_put=False) -> str | None:
    if not f_exit:
        return None
    parts = [f"{_mmdd(exit_iso)} — {sym} ${f_exit['px']:.2f}."]
    if held_n and held_n <= 2:
        parts.append("Same-day / next-day exit.")
    elif held_n:
        parts.append(f"Closed higher {up_days} of the {held_n} days held.")
    rising = f_exit["ma10_rising"] and f_exit["above20"]
    falling = not f_exit["above20"]
    thesis_working = (falling if is_put else rising)
    if rising:
        chart = "The stock was still climbing above its 10-day average"
    elif falling:
        chart = "The stock had rolled over below its 20-day average"
    else:
        chart = "The stock was chopping around its moving averages"
    if held_n and held_n > 2:
        chart += (" — your thesis was still playing out, so this reads as an early exit."
                  if thesis_working else " — the trade was already going against you.")
    else:
        chart += "."
    parts.append(chart)
    if after_rows and f_exit.get("px"):
        base = f_exit["px"]                       # the exit-day close = the baseline for "since exit"
        favorable = min if is_put else max
        ext = favorable(c for _, c in after_rows)
        last = after_rows[-1][1]
        d_ext = round(((base - ext) / base if is_put else (ext - base) / base) * 100)
        parts.append(f"In the {len(after_rows)} trading days after exit it "
                     + (f"{'fell' if is_put else 'ran'} to ${ext:.2f} ({'+' if d_ext>=0 else ''}{d_ext}% your way), last ${last:.2f}." if d_ext >= 5
                        else f"went nowhere (last ${last:.2f})."))
    if ifheld_pct is not None:
        parts.append("Verdict: premature exit." if ifheld_pct >= 5
                     else "Verdict: exiting was right." if ifheld_pct <= -5
                     else "Verdict: about even.")
    return " ".join(parts)


def _days_between(a_iso, b_iso):
    try:
        a = datetime.strptime(a_iso[:10], "%Y-%m-%d").date()
        b = datetime.strptime(b_iso[:10], "%Y-%m-%d").date()
        return (b - a).days
    except (TypeError, ValueError):
        return None


def hold_stats(closes: dict, entry_iso, exit_iso):
    """(up_days, held_n, after_rows) -- after_rows = closes strictly after exit, capped at CHART_WINDOW_DAYS."""
    if not closes or not entry_iso or not exit_iso:
        return 0, 0, []
    rows = _sorted_closes(closes)
    e0, e1 = entry_iso[:10], exit_iso[:10]
    held = [(d, c) for d, c in rows if e0 <= d <= e1]
    up = sum(1 for k in range(1, len(held)) if held[k][1] > held[k - 1][1])
    after = [(d, c) for d, c in rows if d > e1][:CHART_WINDOW_DAYS]
    return up, len(held), after


# ---------------------------------------------------------------------------
# Setup Score (static, technical, entry-only) + Trade Grade (post-mortem)
# ---------------------------------------------------------------------------
GRAVEYARD = {"SOFI", "GLXY", "AMC", "UNG", "BMNR", "AEVA", "QUBT"}  # display flag only, not scored


def _pivots(seq, lr=3):
    hi, lo = [], []
    for i in range(lr, len(seq) - lr):
        w = seq[i - lr:i + lr + 1]
        if seq[i] == max(w):
            hi.append(seq[i])
        if seq[i] == min(w):
            lo.append(seq[i])
    return hi, lo


def pivot_structure(pw: list, entry_iso: str | None) -> str | None:
    """HH/HL, LH/LL, flat, or mixed -- from the ~40 daily bars before entry."""
    if not pw or not entry_iso:
        return None
    pre = [c for d, c in pw if d < entry_iso[:10]][-40:]
    if len(pre) < 20:
        return None
    hi, lo = _pivots(pre)
    if len(hi) < 2 or len(lo) < 2:
        return "flat"
    if hi[-1] > hi[-2] and lo[-1] > lo[-2]:
        return "HH/HL"
    if hi[-1] < hi[-2] and lo[-1] < lo[-2]:
        return "LH/LL"
    return "mixed"


def setup_score(f: dict | None, structure: str | None, is_put: bool):
    """
    Static technical score of the entry, outcome-independent, judged against the
    entry's OWN intent (see entry_intent):

      * continuation -- wants an intact trend with room left; being already
        stretched the trade's way is a demerit (chasing).
      * reversal / fade -- wants an exhausted, OVEREXTENDED move sitting at a
        range extreme, ideally with a turn starting. Here the stretch that hurts
        a continuation is a *plus*.
      * chop -- little location edge; scored mildly on where in the range.

    Returns (score:int clamped to -6..+6, reasons:list[str], criteria:dict).
    """
    if not f:
        return None, [], {}
    intent = entry_intent(f, is_put)
    s, why, crit = 0, [f"{intent} entry"], {"intent": intent, "structure": structure}

    good = "LH/LL" if is_put else "HH/HL"
    bad = "HH/HL" if is_put else "LH/LL"
    move, stretch = _dir_stretch(f, is_put)
    poR = f.get("pct_of_range", 50)
    poR = 100 - poR if is_put else poR

    if intent == "continuation":
        if structure == good:
            s += 3; why.append(f"{good} — trend intact +3"); crit["structure_ok"] = True
        elif structure == bad:
            s -= 3; why.append(f"{bad} — trend broken -3"); crit["structure_ok"] = False
        elif structure == "flat":
            why.append("flat structure 0")
        else:
            s -= 1; why.append("choppy structure -1")
        if (move is not None and move > 35) or stretch > 22:
            s -= 2; why.append(f"already ran {stretch:+.0f}% vs 20-day — chasing -2")
            crit["not_extended"] = False
        else:
            s += 2; why.append("not extended — room to run +2"); crit["not_extended"] = True
        if 55 <= poR <= 90:
            s += 1; why.append("driving, not blown off +1"); crit["range_ok"] = True
        elif poR > 96:
            s -= 1; why.append("buying the blow-off high -1"); crit["range_ok"] = False

    elif intent == "reversal":
        if (move is not None and move < -15) or stretch < -10:
            s += 2; why.append(f"stretched {stretch:+.0f}% the wrong way — mean-reversion fuel +2")
            crit["overextended"] = True
        else:
            why.append("no exhaustion yet 0"); crit["overextended"] = False
        if poR <= 15:
            s += 2; why.append("at the range extreme +2"); crit["range_ok"] = True
        elif poR <= 35:
            s += 1; why.append("near the range extreme +1"); crit["range_ok"] = True
        else:
            s -= 1; why.append("not at an extreme — weak reversal location -1"); crit["range_ok"] = False
        turning = (bool(f.get("crossed_up_20")) or (f.get("ma10_rising") and f.get("pct_off_low", 0) >= 5)) \
            if not is_put else ((not f["above20"]) and not f.get("ma10_rising"))
        if turning:
            s += 2; why.append("turn confirming (MA reclaim / rollover) +2"); crit["turn_signal"] = True
        else:
            why.append("no turn yet — early / knife 0"); crit["turn_signal"] = False
        if structure == bad:
            s -= 1; why.append(f"{bad} still accelerating -1")
        elif structure == "flat":
            s += 1; why.append("basing (flat) +1")

    else:  # chop
        if poR <= 15:
            s += 1; why.append("buying near the low +1"); crit["range_ok"] = True
        elif poR >= 92:
            s -= 1; why.append("buying near the high -1"); crit["range_ok"] = False
        if abs(stretch) > 22:
            s -= 1; why.append("extended in a rangebound tape -1")
        if structure == good:
            s += 1; why.append(f"{good} forming +1")
        elif structure == bad:
            s -= 1; why.append(f"{bad} forming -1")

    s = max(-6, min(6, s))
    tier = "strong" if s >= 4 else "ok" if s >= 1 else "weak"
    crit["tier"] = tier
    return s, why, crit


def trade_grade(f_in, strike, is_put, entry_iso, expiry_iso, days_held, pnl, ifheld_pct):
    """Post-mortem: contract fit + hold discipline + outcome. No ticker history."""
    s, why = 0, []
    if strike is not None and f_in and f_in.get("px"):
        m = (strike / f_in["px"] - 1) * 100
        if is_put:
            m = -m
        if m <= 5:
            s += 2; why.append("ATM/ITM strike +2")
        elif m > 15:
            s -= 2; why.append("deep-OTM strike -2")
    dte = _days_between(entry_iso, expiry_iso)
    if dte is not None:
        if 46 <= dte <= 90:
            s += 1; why.append("46-90 DTE +1")
        elif 21 <= dte < 46 or dte > 90:
            s -= 1; why.append("DTE off (theta / LEAP) -1")
    if days_held is not None:
        if days_held >= 2:
            s += 3; why.append("held >=2 days +3")
        else:
            s -= 4; why.append("flipped <2 days -4")
        if days_held > 21:
            s -= 1; why.append("held >21 days (hoping) -1")
    if pnl is not None and pnl > 0:
        s += 2; why.append("closed green +2")
    if pnl is not None and pnl < 0 and ifheld_pct is not None and ifheld_pct >= 15:
        s -= 1; why.append("cut a would-be winner -1")
    g = "A" if s >= 6 else "B" if s >= 3 else "C" if s >= 0 else "D"
    return g, s, why


# ---------------------------------------------------------------------------
# pipeline output -> Supabase rows
# ---------------------------------------------------------------------------
def _num(v):
    return None if v is None else v


def closed_row(t: dict, now_iso: str, contracts: dict, prices: dict, vix: dict) -> dict:
    c = (contracts.get(str(t.get("exit_order")))
         or contracts.get(str(t.get("entry_order"))) or {})
    px = prices.get(t["sym"], {})
    closes = px.get("closes", {})

    # "if I had held this contract" comparison, measured to the contract's own
    # expiry (or to today if it hasn't expired yet).
    hz = horizon_date(c.get("expiry"), t.get("exit_time"))
    hz_iso = hz.isoformat()
    is_put = c.get("putCall") == "P"
    peak_p, peak_d = favorable_extreme(closes, (t.get("exit_time") or hz_iso), hz_iso, is_put)

    # chart window + templated trend reads
    entry_iso, exit_iso = t.get("entry_time"), t.get("exit_time")
    f_in = trend_features(closes, entry_iso)
    f_out = trend_features(closes, exit_iso)
    setup = classify_setup(f_in, is_put)
    up_d, held_n, after_rows = hold_stats(closes, entry_iso, exit_iso)
    u_ex = close_on_or_before(closes, exit_iso)
    u_hz = close_on_or_before(closes, hz_iso) if exit_iso else None
    ifheld = (((-1 if is_put else 1) * (u_hz - u_ex) / u_ex * 100)
              if (u_ex and u_hz) else None)

    pw = price_window(closes, entry_iso, exit_iso)
    structure = pivot_structure(pw, entry_iso)
    ss, ss_why, ss_crit = setup_score(f_in, structure, is_put)
    tg, tg_pts, tg_why = trade_grade(f_in, c.get("strike"), is_put, entry_iso,
                                     _iso_date(c.get("expiry")), t.get("days_held"),
                                     t.get("pnl"), ifheld)

    return {
        "id": t["id"],
        "symbol": t["sym"],
        "entry_time": t.get("entry_time"),
        "entry_price": _num(t.get("entry_px")),
        "exit_time": t.get("exit_time"),
        "exit_price": _num(t.get("exit_px")),
        "size": _num(t.get("size")),
        "pnl": _num(t.get("pnl")),
        "commission": _num(t.get("comm")),
        "cost_basis": _num(t.get("cost_basis")),
        "return_pct": _num(t.get("return_pct")),
        "days_held": _num(t.get("days_held")),
        "ambiguous": bool(t.get("ambiguous")),
        "ambiguous_reason": t.get("reason"),
        "entry_order_id": t.get("entry_order"),
        "exit_order_id": t.get("exit_order"),
        "contract_expiry": _iso_date(c.get("expiry")),
        "contract_type": c.get("putCall"),
        "contract_strike": _num(c.get("strike")),
        "contract_description": contract_desc(c.get("expiry"), c.get("strike"), c.get("putCall")),
        "underlying_price_entry": close_on_or_before(closes, t.get("entry_time")),
        "underlying_price_exit": close_on_or_before(closes, t.get("exit_time")),
        "underlying_price_latest": px.get("latest"),
        "underlying_price_latest_at": px.get("latest_at"),
        "underlying_price_horizon": close_on_or_before(closes, hz_iso) if t.get("exit_time") else None,
        "contract_horizon_date": hz_iso,
        "underlying_price_peak": peak_p,
        "underlying_peak_date": peak_d,
        "vix_at_entry": close_on_or_before(vix, t.get("entry_time")),
        "vix_at_exit": close_on_or_before(vix, t.get("exit_time")),
        "price_window": pw or None,
        "setup_entry": setup,
        "chart_read_entry": entry_read(t["sym"], f_in, setup, c.get("strike"),
                                       c.get("putCall"), _iso_date(c.get("expiry")), entry_iso),
        "chart_read_exit": exit_read(t["sym"], f_out, up_d, held_n, after_rows,
                                     exit_iso, ifheld, is_put),
        "setup_structure": structure,
        "setup_score": ss,
        "setup_reasons": "; ".join(ss_why) or None,
        "setup_criteria": ss_crit or None,
        "trade_grade": tg,
        "grade_points": tg_pts,
        "grade_reasons": "; ".join(tg_why) or None,
        "trade_seq": t.get("_seq"),
        "synced_at": now_iso,
    }


def _iso_date(v):
    v = (v or "").strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:]}" if len(v) == 8 and v.isdigit() else (v or None)


def open_row(p: dict, now_iso: str, contracts: dict, prices: dict,
             order_by_conid: dict) -> dict:
    # p["id"] is "open_{conid}".
    conid = str(p["id"]).replace("open_", "", 1)
    c = contracts.get(conid, {})

    # the position's own openDateTime is authoritative; fall back to the FIFO
    # match the pipeline found by symbol.
    entry_iso = c.get("open_time") or p.get("entry_time")
    is_put = c.get("put_call") == "P"

    dte = p.get("dte")
    if dte is None:
        dte = dte_from_expiry(c.get("expiry") or "")

    px = prices.get(p["sym"], {})
    closes = px.get("closes", {})
    f_in = trend_features(closes, entry_iso)
    pw = price_window(closes, entry_iso, None, open_ended=True)
    structure = pivot_structure(pw, entry_iso)
    setup = classify_setup(f_in, is_put)
    ss, ss_why, ss_crit = setup_score(f_in, structure, is_put)

    return {
        "id": p["id"],
        "symbol": p["sym"],
        "contract_description": p.get("desc"),
        "contract_type": c.get("put_call"),
        "contract_strike": _num(c.get("strike")),
        "contract_expiry": _iso_date(c.get("expiry")),
        "entry_time": entry_iso,
        "entry_price": _num(p.get("entry_px")),
        "cost_basis": _num(p.get("cost_basis")),
        "market_value": _num(p.get("market_value")),
        "unrealized_pnl": _num(p.get("unrealized_pnl")),
        "daily_pnl": _num(p.get("daily_pnl")),
        "dte": _num(dte),
        "underlying_price_entry": close_on_or_before(closes, entry_iso),
        "underlying_price_latest": px.get("latest"),
        "underlying_price_latest_at": px.get("latest_at"),
        "price_window": pw or None,
        "setup_structure": structure,
        "setup_score": ss,
        "setup_reasons": "; ".join(ss_why) or None,
        "setup_criteria": ss_crit or None,
        "setup_entry": setup,
        "chart_read_entry": entry_read(p["sym"], f_in, setup, c.get("strike"),
                                       c.get("put_call"), _iso_date(c.get("expiry")), entry_iso),
        "unverified_entry_date": bool(p.get("unverified_entry_date")) and not c.get("open_time"),
        "entry_order_id": order_by_conid.get(conid),
        "synced_at": now_iso,
    }


# ---------------------------------------------------------------------------
# Supabase (PostgREST, secret key -> bypasses RLS)
# ---------------------------------------------------------------------------
class Supabase:
    def __init__(self, url: str, secret_key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }

    def upsert(self, table: str, rows: list[dict], chunk: int = 500) -> None:
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            r = requests.post(
                f"{self.base}/{table}",
                params={"on_conflict": "id"},
                headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=batch,
                timeout=HTTP_TIMEOUT,
            )
            if not r.ok:
                die(f"Supabase upsert {table} failed ({r.status_code}): {r.text[:300]}")

    def get(self, table: str, select: str) -> list[dict]:
        r = requests.get(f"{self.base}/{table}", params={"select": select},
                         headers=self.headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def delete_not_in(self, table: str, keep_ids: list[str]) -> int:
        """Delete rows whose id is not in keep_ids (delete all if keep_ids empty)."""
        if keep_ids:
            quoted = ",".join('"' + i.replace('"', '') + '"' for i in keep_ids)
            params = {"id": f"not.in.({quoted})"}
        else:
            params = {"id": "not.is.null"}
        r = requests.delete(
            f"{self.base}/{table}",
            params=params,
            headers={**self.headers, "Prefer": "return=representation"},
            timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            die(f"Supabase delete {table} failed ({r.status_code}): {r.text[:300]}")
        try:
            return len(r.json())
        except ValueError:
            return 0


# ---------------------------------------------------------------------------
def main() -> None:
    token = os.environ.get("FLEX_TOKEN") or die("FLEX_TOKEN not set")
    query_id = os.environ.get("FLEX_QUERY_ID", "1618686")
    supabase_url = os.environ.get("SUPABASE_URL") or die("SUPABASE_URL not set")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY") or die("SUPABASE_SECRET_KEY not set")

    tz_name = os.environ.get("FLEX_TIMEZONE", "America/New_York")
    tz = ZoneInfo(tz_name) if ZoneInfo else timezone.utc

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    log(f"fetching Flex query {query_id}")
    root = fetch_flex_statement(token, query_id)

    raw_trades = flex_to_raw_trades(root, tz)
    raw_positions = flex_to_raw_positions(root)
    pos_contracts = flex_position_contract_by_conid(raw_positions, tz)
    log(f"parsed {len(raw_trades)} trade executions, {len(raw_positions)} open positions")

    if not raw_trades and not raw_positions:
        die("Flex returned no trades and no positions -- refusing to wipe tables. "
            "Check the query date range and that the account has activity.")

    closed, opened = run_pipeline(raw_trades, raw_positions)
    ambiguous = sum(1 for t in closed if t.get("ambiguous"))
    log(f"pipeline: {len(closed)} closed round-trips ({ambiguous} ambiguous), {len(opened)} open positions")

    # --- enrich closed trades -------------------------------------------------
    contracts = flex_contract_by_order(root)
    order_by_conid = {p["contract_id"]: p.get("entry_order_id")
                      for p in raw_positions if p.get("contract_id")}

    sb = Supabase(supabase_url, secret_key)

    # journal notes currently on open positions -> carry to the round-trip when
    # it closes. Keyed by entry_order_id (when Flex provides it) and, as a
    # fallback, by "symbol|contract_description" (the specific option contract).
    open_notes = {}
    JCOLS = ("strategy", "journal_thoughts", "planned_stop", "planned_target")
    try:
        cur = sb.get("open_positions", "entry_order_id,symbol,contract_description," + ",".join(JCOLS))
        for r in cur:
            if not any(r.get(k) is not None for k in JCOLS):
                continue
            note = {k: r.get(k) for k in JCOLS}
            if r.get("entry_order_id"):
                open_notes[r["entry_order_id"]] = note
            if r.get("symbol") and r.get("contract_description"):
                open_notes[f"{r['symbol']}|{r['contract_description']}"] = note
    except Exception as e:  # noqa: BLE001
        log(f"  (could not read open-position notes: {e})")

    # chronological sequence number (Nth closed round-trip to date)
    for i, t in enumerate(sorted(closed, key=lambda x: (x.get("exit_time") or x.get("entry_time") or "")), 1):
        t["_seq"] = i

    tickers = {t["sym"] for t in closed if t.get("sym")}
    tickers |= {p["sym"] for p in opened if p.get("sym")}
    dates = [(t.get("entry_time") or t.get("exit_time") or "")[:10] for t in closed]
    dates += [(pos_contracts.get(str(p["id"]).replace("open_", "", 1), {}).get("open_time")
               or p.get("entry_time") or "")[:10] for p in opened]
    earliest = min((d for d in dates if d), default=datetime.now(timezone.utc).date().isoformat())
    since_date = datetime.strptime(earliest, "%Y-%m-%d").date()
    log(f"fetching underlying prices for {len(tickers)} tickers (Yahoo)")
    prices = fetch_underlying_prices(tickers, since_date)
    priced = sum(1 for p in prices.values() if p.get("latest") is not None)
    log(f"  got current price for {priced}/{len(tickers)} tickers")
    vix = fetch_vix(since_date)
    log(f"  VIX history: {len(vix)} days")

    closed_rows = []
    carried = 0
    for t in closed:
        row = closed_row(t, now_iso, contracts, prices, vix)
        note = (open_notes.get(t.get("entry_order"))
                or open_notes.get(f"{row['symbol']}|{row.get('contract_description')}"))
        if note:
            row.update({k: v for k, v in note.items() if v is not None})
            carried += 1
        closed_rows.append(row)
    sb.upsert("closed_trades", closed_rows)
    log(f"closed_trades: upserted {len(closed_rows)} rows "
        f"({carried} carried journal notes from a just-closed position)")

    open_rows = [open_row(p, now_iso, pos_contracts, prices, order_by_conid) for p in opened]
    if open_rows:
        sb.upsert("open_positions", open_rows)
    removed = sb.delete_not_in("open_positions", [r["id"] for r in open_rows])
    log(f"open_positions: upserted {len(open_rows)}, removed {removed} stale")

    usdcad = fetch_usdcad_rate()
    acct = flex_to_account_summary(root, now_iso, usdcad)
    if acct is None:
        log("account_summary: NAV / Cash Report sections not in the Flex query -- skipped")
    else:
        sb.upsert("account_summary", [acct])
        log(f"account_summary: net_liq={acct.get('net_liquidation')} "
            f"avail={acct.get('available_funds')} {acct.get('currency')} "
            f"(fx usdcad={usdcad} -> net_liq_usd={acct.get('net_liquidation_usd')} "
            f"avail_usd={acct.get('available_funds_usd')})")

    log("done")


if __name__ == "__main__":
    main()
