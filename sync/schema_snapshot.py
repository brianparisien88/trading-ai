"""
Write docs/schema-snapshot.json from the live Supabase public schema.

Deterministic, no LLM. Run in CI (weekly) to detect doc drift, or locally after
a migration. Calls the `schema_catalog()` RPC (defined in
supabase/migrations/*_schema_catalog_rpc.sql) so it needs only the REST API +
secret key -- no raw Postgres connection.

Env: SUPABASE_URL, SUPABASE_SECRET_KEY
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import requests

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "schema-snapshot.json"


def main() -> None:
    url = os.environ.get("SUPABASE_URL") or sys.exit("SUPABASE_URL not set")
    key = os.environ.get("SUPABASE_SECRET_KEY") or sys.exit("SUPABASE_SECRET_KEY not set")
    r = requests.post(
        f"{url.rstrip('/')}/rest/v1/rpc/schema_catalog",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={}, timeout=30,
    )
    r.raise_for_status()
    catalog = r.json()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    n_tables = len(catalog)
    n_cols = sum(len(v) for v in catalog.values())
    print(f"wrote {OUT.relative_to(OUT.parent.parent)} -- {n_tables} tables, {n_cols} columns")


if __name__ == "__main__":
    main()
