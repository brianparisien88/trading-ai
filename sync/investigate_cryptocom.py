"""
TEMPORARY investigation script -- not part of the pipeline. Checks whether
the user-supplied Crypto.com address appears ANYWHERE in the wallet's full
transaction history (any operation type, any direction, any token) on our
currently-tracked chains (ethereum, binance-smart-chain). If it never
appears, the deposit likely went out on a chain we don't track. Read-only,
does not touch the DB. Delete after use.
"""
from __future__ import annotations

import os

from onchain import zpaged, _fung, _chain_of

WALLET = (os.environ.get("ONCHAIN_WALLET") or "0x3414ec2d1c63008e1cda0e2155b7334c446a0025").lower()
CHAINS = os.environ.get("ONCHAIN_CHAINS") or "ethereum,binance-smart-chain"
TARGET = "0x1c3eba9f65b60a1bd6061f177f9f7f6c0c8195aa"  # user-supplied Crypto.com address, lowercased


def main():
    # no operation_types filter this time -- check every kind of transaction
    raw = zpaged(f"/wallets/{WALLET}/transactions/", {
        "currency": "usd",
        "filter[chain_ids]": CHAINS,
        "page[size]": 100,
    })
    print(f"total txns fetched (all types, all directions, tracked chains): {len(raw)}")

    hits = []
    for tx in raw:
        a = tx.get("attributes") or {}
        if (a.get("sent_from") or "").lower() == TARGET or (a.get("sent_to") or "").lower() == TARGET:
            hits.append(("tx-level", tx))
            continue
        for tr in a.get("transfers") or []:
            if (tr.get("recipient") or "").lower() == TARGET or (tr.get("sender") or "").lower() == TARGET:
                hits.append(("transfer-level", tx))
                break

    print(f"\ntransactions touching {TARGET}: {len(hits)}")
    for kind, tx in hits[:20]:
        a = tx.get("attributes") or {}
        print(f"  [{kind}] hash={a.get('hash')} op={a.get('operation_type')} chain={_chain_of(tx)} "
              f"mined_at={a.get('mined_at')}")
        for tr in a.get("transfers") or []:
            f = _fung(tr)
            print(f"      transfer dir={tr.get('direction')} sym={f['symbol']} value={tr.get('value')} "
                  f"recipient={tr.get('recipient')} sender={tr.get('sender')}")


if __name__ == "__main__":
    main()
