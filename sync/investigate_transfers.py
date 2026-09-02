"""
TEMPORARY investigation script -- not part of the pipeline, not imported by
anything. Fetches non-trade wallet activity (send/receive/deposit/withdraw)
from Zerion to see whether real-world cash-outs are large enough to matter
for how we present realized P&L. Prints a summary to the Actions log; does
not touch the DB. Delete after use.
"""
from __future__ import annotations

import os
from collections import defaultdict

from onchain import zpaged, _fung, _chain_of, is_cashlike  # reuse existing helpers

WALLET = (os.environ.get("ONCHAIN_WALLET") or "0x3414ec2d1c63008e1cda0e2155b7334c446a0025").lower()
CHAINS = os.environ.get("ONCHAIN_CHAINS") or "ethereum,binance-smart-chain"


def main():
    raw = zpaged(f"/wallets/{WALLET}/transactions/", {
        "currency": "usd",
        "filter[operation_types]": "send,receive,deposit,withdraw",
        "filter[chain_ids]": CHAINS,
        "page[size]": 100,
    })
    print(f"total non-trade txns fetched: {len(raw)}")

    by_type = defaultdict(lambda: {"n": 0, "usd": 0.0})
    outbound_cashlike = []  # sends of a stable/native token -- the likely "cashed out to spend" bucket
    outbound_other = []     # sends of a volatile token -- ambiguous (bridge? gift? spend directly?)

    for tx in raw:
        a = tx.get("attributes") or {}
        op = a.get("operation_type")
        mined = a.get("mined_at")
        transfers = a.get("transfers") or []
        tx_usd = sum(abs(tr.get("value") or 0) for tr in transfers)
        by_type[op]["n"] += 1
        by_type[op]["usd"] += tx_usd

        if op == "send":
            for tr in transfers:
                if tr.get("direction") != "out":
                    continue
                f = _fung(tr)
                val = tr.get("value")
                row = {"time": mined, "symbol": f["symbol"], "usd": val, "hash": a.get("hash")}
                if is_cashlike(f["symbol"]):
                    outbound_cashlike.append(row)
                else:
                    outbound_other.append(row)

    print("\n-- by operation_type --")
    for op, d in sorted(by_type.items(), key=lambda kv: -kv[1]["usd"]):
        print(f"  {op or '(none)'}: n={d['n']}  total_usd=${d['usd']:,.2f}")

    print(f"\n-- outbound SEND of stable/native (likely cash-out to spend): n={len(outbound_cashlike)} "
          f"total=${sum(r['usd'] or 0 for r in outbound_cashlike):,.2f} --")
    for r in sorted(outbound_cashlike, key=lambda r: -(r['usd'] or 0))[:15]:
        print(f"  {r['time']}  {r['symbol']:>6}  ${r['usd']:>10,.2f}  {r['hash']}")

    print(f"\n-- outbound SEND of a volatile token (ambiguous -- bridge/gift/direct spend?): "
          f"n={len(outbound_other)} total=${sum(r['usd'] or 0 for r in outbound_other):,.2f} --")
    for r in sorted(outbound_other, key=lambda r: -(r['usd'] or 0))[:15]:
        print(f"  {r['time']}  {r['symbol']:>6}  ${r['usd']:>10,.2f}  {r['hash']}")


if __name__ == "__main__":
    main()
