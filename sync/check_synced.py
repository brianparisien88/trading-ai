"""
Fallback-trigger guard for sync.yml's second (backup) cron tick.

sync.yml fires twice: a primary run and, ~14 minutes later, a fallback tick
whose only job is to check whether the primary actually ran today (GitHub
Actions schedules are best-effort and can be delayed, especially at common
trigger minutes -- see the workflow file). This script is that check.

Prints exactly one word to stdout:
  "skip"  -- account_summary already shows a sync from today (UTC); the
             primary tick worked, the fallback should do nothing.
  "run"   -- no sync yet today (or the check itself failed) -- proceed.

Fails OPEN on purpose: if Supabase can't be reached, credentials are
missing, or anything about the check goes wrong, this prints "run" rather
than silently skipping a day's sync. Never raises -- a broken check must
never be the reason data goes stale.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import requests

HTTP_TIMEOUT = 20


def main() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        print("run", file=sys.stderr)  # visible in the Actions log too
        print("run")
        return

    try:
        r = requests.get(
            f"{url.rstrip('/')}/rest/v1/account_summary",
            params={"select": "synced_at", "id": "eq.current"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
        synced_at = rows[0].get("synced_at") if rows else None
        if synced_at and synced_at[:10] == datetime.now(timezone.utc).date().isoformat():
            print(f"already synced today ({synced_at}) -- skipping fallback run", file=sys.stderr)
            print("skip")
            return
        print(f"no sync today yet (last: {synced_at}) -- fallback will run", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - fail open, see module docstring
        print(f"check failed ({e}) -- fail open, fallback will run", file=sys.stderr)

    print("run")


if __name__ == "__main__":
    main()
