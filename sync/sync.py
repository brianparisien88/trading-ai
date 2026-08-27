"""
Deterministic IBKR -> Supabase sync.

Flow (no LLM, no interactive auth):
  1. Pull an Activity Flex Query (Trades + Open Positions) from IBKR's
     Flex Web Service using a token.
  2. Parse the XML into the raw_trades / raw_positions dict shapes that
     trade_pipeline.py expects.
  3. run_pipeline() -> (closed_trades, open_positions).
  4. Upsert closed_trades (dedup on stable id; never touches the user's
     journal columns) and reconcile open_positions (upsert current, delete
     stale) in Supabase via the secret key (bypasses RLS).

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
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None

import requests

from trade_pipeline import run_pipeline

FLEX_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
FLEX_V = "3"
USER_AGENT = "trade-journal-sync/1.0"
HTTP_TIMEOUT = 60

# Columns the sync job owns on closed_trades. Journal columns
# (strategy, journal_thoughts, planned_stop, planned_target) and
# contract_description are deliberately absent so an upsert never
# overwrites what the user typed in the dashboard.
CLOSED_SYNC_COLUMNS = [
    "id", "symbol", "entry_time", "entry_price", "exit_time", "exit_price",
    "size", "pnl", "commission", "cost_basis", "return_pct", "days_held",
    "ambiguous", "ambiguous_reason", "entry_order_id", "exit_order_id",
    "synced_at",
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


# ---------------------------------------------------------------------------
# pipeline output -> Supabase rows
# ---------------------------------------------------------------------------
def _num(v):
    return None if v is None else v


def closed_row(t: dict, now_iso: str) -> dict:
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
        "synced_at": now_iso,
    }


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

    sb = Supabase(supabase_url, secret_key)

    closed_rows = [closed_row(t, now_iso) for t in closed]
    sb.upsert("closed_trades", closed_rows)
    log(f"closed_trades: upserted {len(closed_rows)} rows (journal columns untouched)")

    open_rows = [open_row(p, now_iso, expiry_by_conid) for p in opened]
    if open_rows:
        sb.upsert("open_positions", open_rows)
    removed = sb.delete_not_in("open_positions", [r["id"] for r in open_rows])
    log(f"open_positions: upserted {len(open_rows)}, removed {removed} stale")

    log("done")


if __name__ == "__main__":
    main()
