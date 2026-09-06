"""
TEMPORARY investigation script -- not part of the pipeline. Scopes to the
actual "Pay Yourself" tracking window (2025-11-01 -> now) and stablecoins
only (not ETH/BNB), grouping outbound sends by destination so the user can
identify which address(es) are Crypto.com vs Lulubit. Read-only, does not
touch the DB. Delete after use.
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone

from onchain import zpaged, _fung, is_stable

WALLET = (os.environ.get("ONCHAIN_WALLET") or "0x3414ec2d1c63008e1cda0e2155b7334c446a0025").lower()
CHAINS = os.environ.get("ONCHAIN_CHAINS") or "ethereum,binance-smart-chain"
START = datetime(2025, 11, 1, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)


def main():
    raw = zpaged(f"/wallets/{WALLET}/transactions/", {
        "currency": "usd",
        "filter[operation_types]": "send",
        "filter[chain_ids]": CHAINS,
        "filter[min_mined_at]": START_MS,
        "page[size]": 100,
    })
    print(f"send txns since {START.date()}: {len(raw)}")

    stats = defaultdict(lambda: {"usd": 0.0, "n": 0, "first": None, "last": None, "syms": set()})
    for tx in raw:
        a = tx.get("attributes") or {}
        mined = a.get("mined_at")
        for tr in a.get("transfers") or []:
            if tr.get("direction") != "out":
                continue
            f = _fung(tr)
            if not is_stable(f["symbol"]):   # stablecoins only, not ETH/BNB, per this feature's scope
                continue
            addr = tr.get("recipient") or "UNKNOWN"
            s = stats[addr]
            s["usd"] += tr.get("value") or 0
            s["n"] += 1
            s["syms"].add(f["symbol"])
            if s["first"] is None or mined < s["first"]:
                s["first"] = mined
            if s["last"] is None or mined > s["last"]:
                s["last"] = mined

    ranked = sorted(stats.items(), key=lambda kv: -kv[1]["usd"])
    total = sum(s["usd"] for _, s in ranked)
    print(f"\n{len(ranked)} unique destination addresses, ${total:,.2f} total stablecoin sent out since {START.date()}")
    print("\naddress | total $ | n txns | symbols | first | last")
    for addr, s in ranked:
        print(f"  {addr}  ${s['usd']:,.2f}  n={s['n']}  {sorted(s['syms'])}  "
              f"first={s['first']}  last={s['last']}")


if __name__ == "__main__":
    main()
