"""
TEMPORARY investigation script -- not part of the pipeline. Dumps the raw
attributes of a few real 'send' transactions to see whether Zerion exposes a
counterparty/recipient address, then groups all outbound stable/native sends
by destination so the user can label which addresses are exchanges (real
cash-out) vs their own other wallet (Exodus, not a cash-out). Read-only,
does not touch the DB. Delete after use.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from onchain import zpaged, _fung, _chain_of, is_cashlike

WALLET = (os.environ.get("ONCHAIN_WALLET") or "0x3414ec2d1c63008e1cda0e2155b7334c446a0025").lower()
CHAINS = os.environ.get("ONCHAIN_CHAINS") or "ethereum,binance-smart-chain"


def main():
    raw = zpaged(f"/wallets/{WALLET}/transactions/", {
        "currency": "usd",
        "filter[operation_types]": "send",
        "filter[chain_ids]": CHAINS,
        "page[size]": 100,
    })
    print(f"total send txns: {len(raw)}")

    # dump full raw attributes of the first 3 -- looking for any
    # recipient/counterparty address field, whatever it's called
    print("\n== RAW SAMPLE (first 3 send txns, full attributes) ==")
    for tx in raw[:3]:
        print(json.dumps(tx.get("attributes"), indent=1, default=str)[:3000])
        print("---")

    # group outbound stable/native sends by whatever address field we can find
    dest_totals = defaultdict(float)
    dest_examples = defaultdict(list)
    candidate_keys = set()
    for tx in raw:
        a = tx.get("attributes") or {}
        for tr in a.get("transfers") or []:
            if tr.get("direction") != "out":
                continue
            f = _fung(tr)
            if not is_cashlike(f["symbol"]):
                continue
            val = tr.get("value") or 0
            # collect every key on the transfer object that looks address-like
            for k in tr.keys():
                if "address" in k.lower() or "recipient" in k.lower() or "to" == k.lower() or "counterpart" in k.lower():
                    candidate_keys.add(k)
            dest = (tr.get("recipient") or tr.get("to") or tr.get("counterparty")
                    or tr.get("recipient_address") or "UNKNOWN")
            dest_totals[dest] += val
            if len(dest_examples[dest]) < 2:
                dest_examples[dest].append({"time": a.get("mined_at"), "symbol": f["symbol"], "usd": val, "hash": a.get("hash")})

    print(f"\naddress-like keys found on transfer objects: {candidate_keys}")
    print("\n== outbound stable/native sends grouped by destination ==")
    for dest, total in sorted(dest_totals.items(), key=lambda kv: -kv[1]):
        print(f"  {dest}: ${total:,.2f}  (examples: {dest_examples[dest]})")


if __name__ == "__main__":
    main()
