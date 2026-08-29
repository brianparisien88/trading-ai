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
    "underlying_price_peak", "underlying_peak_date", "trade_seq", "synced_at",
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


def flex_expiry_by_conid(root: ET.Element) -> dict:
    """conid -> expiry date (yyyyMMdd string) from the open positions."""
    out = {}
    for pos in root.iter("OpenPosition"):
        a = pos.attrib
        exp = (a.get("expiry") or "").strip()
        if a.get("conid") and exp:
            out[a["conid"]] = exp
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
        })
    return out


def flex_to_account_summary(root: ET.Element, now_iso: str) -> dict | None:
    """
    Account-level balances in BASE currency, from the NAV + Cash Report sections.
      net_liquidation  <- latest EquitySummaryByReportDateInBase.total
      available_funds  <- CashReport BASE_SUMMARY endingSettledCash (fallback endingCash)
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


def fetch_underlying_prices(tickers: set, since: "datetime.date") -> dict:
    """
    One Yahoo chart call per ticker. Returns
      { ticker: {"latest": float|None, "latest_at": iso|None, "closes": {yyyy-mm-dd: float}} }
    Best-effort: a failed ticker yields empty data, never raises.
    """
    p1 = int((datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
              - timedelta(days=7)).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    out = {}
    for t in sorted(tickers):
        rec = {"latest": None, "latest_at": None, "closes": {}}
        for attempt in (1, 2):
            try:
                r = requests.get(
                    f"{YF_CHART}{t}",
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
                    log(f"  yahoo {t}: {e}")
        out[t] = rec
        time.sleep(0.25)
    return out


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
# pipeline output -> Supabase rows
# ---------------------------------------------------------------------------
def _num(v):
    return None if v is None else v


def closed_row(t: dict, now_iso: str, contracts: dict, prices: dict) -> dict:
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
        "trade_seq": t.get("_seq"),
        "synced_at": now_iso,
    }


def _iso_date(v):
    v = (v or "").strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:]}" if len(v) == 8 and v.isdigit() else (v or None)


def open_row(p: dict, now_iso: str, expiry_by_conid: dict) -> dict:
    # p["id"] is "open_{conid}". Prefer the pipeline's dte; fall back to the
    # explicit Flex expiry attribute (the pipeline's parser expects the old
    # IBKR-MCP description format and won't match Flex's).
    dte = p.get("dte")
    if dte is None:
        conid = str(p["id"]).replace("open_", "", 1)
        dte = dte_from_expiry(expiry_by_conid.get(conid, ""))
    return {
        "id": p["id"],
        "symbol": p["sym"],
        "contract_description": p.get("desc"),
        "entry_time": p.get("entry_time"),
        "entry_price": _num(p.get("entry_px")),
        "cost_basis": _num(p.get("cost_basis")),
        "market_value": _num(p.get("market_value")),
        "unrealized_pnl": _num(p.get("unrealized_pnl")),
        "daily_pnl": _num(p.get("daily_pnl")),
        "dte": _num(dte),
        "unverified_entry_date": bool(p.get("unverified_entry_date")),
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
    expiry_by_conid = flex_expiry_by_conid(root)
    log(f"parsed {len(raw_trades)} trade executions, {len(raw_positions)} open positions")

    if not raw_trades and not raw_positions:
        die("Flex returned no trades and no positions -- refusing to wipe tables. "
            "Check the query date range and that the account has activity.")

    closed, opened = run_pipeline(raw_trades, raw_positions)
    ambiguous = sum(1 for t in closed if t.get("ambiguous"))
    log(f"pipeline: {len(closed)} closed round-trips ({ambiguous} ambiguous), {len(opened)} open positions")

    # --- enrich closed trades -------------------------------------------------
    contracts = flex_contract_by_order(root)

    # chronological sequence number (Nth closed round-trip to date)
    for i, t in enumerate(sorted(closed, key=lambda x: (x.get("exit_time") or x.get("entry_time") or "")), 1):
        t["_seq"] = i

    tickers = {t["sym"] for t in closed if t.get("sym")}
    dates = [(t.get("entry_time") or t.get("exit_time") or "")[:10] for t in closed]
    earliest = min((d for d in dates if d), default=datetime.now(timezone.utc).date().isoformat())
    log(f"fetching underlying prices for {len(tickers)} tickers (Yahoo)")
    prices = fetch_underlying_prices(tickers, datetime.strptime(earliest, "%Y-%m-%d").date())
    priced = sum(1 for p in prices.values() if p.get("latest") is not None)
    log(f"  got current price for {priced}/{len(tickers)} tickers")

    sb = Supabase(supabase_url, secret_key)

    closed_rows = [closed_row(t, now_iso, contracts, prices) for t in closed]
    sb.upsert("closed_trades", closed_rows)
    log(f"closed_trades: upserted {len(closed_rows)} rows (journal columns untouched)")

    open_rows = [open_row(p, now_iso, expiry_by_conid) for p in opened]
    if open_rows:
        sb.upsert("open_positions", open_rows)
    removed = sb.delete_not_in("open_positions", [r["id"] for r in open_rows])
    log(f"open_positions: upserted {len(open_rows)}, removed {removed} stale")

    acct = flex_to_account_summary(root, now_iso)
    if acct is None:
        log("account_summary: NAV / Cash Report sections not in the Flex query -- skipped")
    else:
        sb.upsert("account_summary", [acct])
        log(f"account_summary: net_liq={acct.get('net_liquidation')} "
            f"avail={acct.get('available_funds')} {acct.get('currency')}")

    log("done")


if __name__ == "__main__":
    main()
