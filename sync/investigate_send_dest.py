"""
TEMPORARY investigation script v3 -- not part of the pipeline. For every
counterparty address the wallet has sent stable/native funds to OR received
stable/native funds from, tally both directions so a back-and-forth pattern
(a personal wallet like Exodus) is visually distinguishable from a one-way
cash-out (an exchange). Read-only, does not touch the DB. Delete after use.
"""
from __future__ import annotations

import os
from collections import defaultdict

from onchain import zpaged, _fung, is_cashlike

WALLET = (os.environ.get("ONCHAIN_WALLET") or "0x3414ec2d1c63008e1cda0e2155b7334c446a0025").lower()
CHAINS = os.environ.get("ONCHAIN_CHAINS") or "ethereum,binance-smart-chain"


def fetch(op_type):
    return zpaged(f"/wallets/{WALLET}/transactions/", {
        "currency": "usd",
        "filter[operation_types]": op_type,
        "filter[chain_ids]": CHAINS,
        "page[size]": 100,
    })


def main():
    sends = fetch("send")
    receives = fetch("receive")
    print(f"send txns: {len(sends)}, receive txns: {len(receives)}")

    # addr -> {"out_usd","out_n","in_usd","in_n"}
    stats = defaultdict(lambda: {"out_usd": 0.0, "out_n": 0, "in_usd": 0.0, "in_n": 0})

    for tx in sends:
        a = tx.get("attributes") or {}
        for tr in a.get("transfers") or []:
            if tr.get("direction") != "out":
                continue
            f = _fung(tr)
            if not is_cashlike(f["symbol"]):
                continue
            addr = tr.get("recipient") or "UNKNOWN"
            stats[addr]["out_usd"] += tr.get("value") or 0
            stats[addr]["out_n"] += 1

    for tx in receives:
        a = tx.get("attributes") or {}
        for tr in a.get("transfers") or []:
            if tr.get("direction") != "in":
                continue
            f = _fung(tr)
            if not is_cashlike(f["symbol"]):
                continue
            addr = tr.get("sender") or "UNKNOWN"
            stats[addr]["in_usd"] += tr.get("value") or 0
            stats[addr]["in_n"] += 1

    ranked = sorted(stats.items(), key=lambda kv: -(kv[1]["out_usd"] + kv[1]["in_usd"]))
    print(f"\n{len(ranked)} unique counterparty addresses (stable/native only)")
    print("\n== top 30 by total volume (both directions) ==")
    print(f"{'address':<44} {'sent->addr':>14} {'(n)':>5} {'recv<-addr':>14} {'(n)':>5}  back-and-forth?")
    for addr, s in ranked[:30]:
        bf = "YES" if (s["out_n"] > 0 and s["in_n"] > 0) else ""
        print(f"{addr:<44} ${s['out_usd']:>12,.2f} {s['out_n']:>5} ${s['in_usd']:>12,.2f} {s['in_n']:>5}  {bf}")


if __name__ == "__main__":
    main()
