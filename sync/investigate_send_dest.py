"""
TEMPORARY investigation script v2 -- not part of the pipeline. Focuses on
just the fields that matter: tx-level sent_from/sent_to (which for an ERC20
transfer is the TOKEN CONTRACT, not the human recipient) and every field on
each transfer leg, to find the actual counterparty wallet address. Read-only,
does not touch the DB. Delete after use.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from onchain import zpaged, _fung, is_cashlike

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

    print("\n== first 5 sends: tx-level + full transfer leg dump ==")
    for tx in raw[:5]:
        a = tx.get("attributes") or {}
        print(f"hash={a.get('hash')} sent_from={a.get('sent_from')} sent_to={a.get('sent_to')}")
        for tr in a.get("transfers") or []:
            f = _fung(tr)
            print(f"  transfer: dir={tr.get('direction')} sym={f['symbol']} value={tr.get('value')} "
                  f"keys={sorted(tr.keys())}")
            print(f"    full: {json.dumps(tr, default=str)}")
        print("---")

    # group outbound stable/native sends by every plausible destination field
    dest_totals = defaultdict(float)
    for tx in raw:
        a = tx.get("attributes") or {}
        for tr in a.get("transfers") or []:
            if tr.get("direction") != "out":
                continue
            f = _fung(tr)
            if not is_cashlike(f["symbol"]):
                continue
            val = tr.get("value") or 0
            dest = tr.get("recipient") or tr.get("sender") or a.get("sent_to") or "UNKNOWN"
            dest_totals[dest] += val

    print("\n== outbound stable/native sends grouped (best-guess destination field) ==")
    for dest, total in sorted(dest_totals.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {dest}: ${total:,.2f}")


if __name__ == "__main__":
    main()
