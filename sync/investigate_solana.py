"""
TEMPORARY investigation script -- not part of the pipeline. Confirms Zerion
supports querying a Solana wallet through the same /wallets/{address}/...
endpoint shape as EVM wallets, and checks whether any sends to the Lulubit
Solana address show up. Read-only, does not touch the DB. Delete after use.
"""
from __future__ import annotations

from onchain import zpaged, _fung, is_stable

SOL_WALLET = "Ft2TgrJ6i9oU3gdi5wxnsH8Q8RWgu8qzKrbx5gUgAK4e"
LULUBIT_SOL = "FdfNcbrRHqCnnfhA79R49DCT2AKuRWyd3TcsrwSZzuN"


def main():
    raw = zpaged(f"/wallets/{SOL_WALLET}/transactions/", {
        "currency": "usd",
        "filter[operation_types]": "send",
        "filter[chain_ids]": "solana",
        "page[size]": 100,
    })
    print(f"send txns on Solana for this wallet: {len(raw)}")

    hits = 0
    total_to_lulubit = 0.0
    for tx in raw:
        a = tx.get("attributes") or {}
        for tr in a.get("transfers") or []:
            if tr.get("direction") != "out":
                continue
            recip = tr.get("recipient") or ""
            f = _fung(tr)
            if recip == LULUBIT_SOL:
                hits += 1
                val = tr.get("value") or 0
                if is_stable(f["symbol"]):
                    total_to_lulubit += val
                print(f"  hit: sym={f['symbol']} value={val} stable={is_stable(f['symbol'])} "
                      f"mined_at={a.get('mined_at')} recipient={recip}")

    print(f"\ntotal stablecoin sent to {LULUBIT_SOL}: ${total_to_lulubit:,.2f} ({hits} matching transfers)")

    # also sanity-check positions endpoint works for this address/chain
    pos = zpaged(f"/wallets/{SOL_WALLET}/positions/", {
        "currency": "usd", "filter[chain_ids]": "solana", "filter[position_types]": "wallet",
        "page[size]": 100,
    })
    print(f"\npositions endpoint returned {len(pos)} rows for this wallet on solana")


if __name__ == "__main__":
    main()
