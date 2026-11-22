#!/usr/bin/env python3
"""Load the committed demo dataset through the real HTTP ingestion pipeline.

    python3 scripts/load_demo.py [--api http://localhost:8000] [--force]

* Uses only the standard library (no virtualenv needed) and the public API:
  ``POST /ingest`` → background data-quality analysis → correlation.
* Refuses to run when the database already contains events, so demo data is
  never mixed into existing data by accident. ``--force`` overrides this
  (re-loading the same file records every record as a duplicate submission).
* Waits for the background analysis and correlation, then prints what to open.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "data" / "samples" / "demo_events.json"


def call(api: str, path: str, data: bytes | None = None, content_type: str = "application/json") -> dict:
    req = urllib.request.Request(api + path, data=data, headers={"Content-Type": content_type} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # 422 = every record invalid; body still carries the summary
        return json.loads(exc.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000", help="backend base URL")
    parser.add_argument("--force", action="store_true", help="load even if the database already contains events")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    try:
        health = call(api, "/health")
    except (urllib.error.URLError, OSError) as exc:
        print(f"Backend not reachable at {api}: {exc}. Start it first (see README: Run locally).", file=sys.stderr)
        return 1
    print(f"backend: {health.get('status')} ({api})")

    existing = call(api, "/events?page_size=1")["total"]
    if existing and not args.force:
        print(f"The database already contains {existing:,} events; nothing was loaded. Use --force to load the demo data anyway.")
        return 2

    summary = call(api, "/ingest?filename=demo_events.json", DEMO.read_bytes())
    summary = summary.get("error", {}).get("details", summary)
    print(f"ingested {summary['total_received']} submissions: {summary['accepted']} accepted, {summary['invalid']} invalid, "
          f"{summary['duplicate_event_ids']} duplicate event IDs")

    print("waiting for the background data-quality analysis and correlation ...", flush=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        quality = call(api, "/quality-issues/summary")
        incidents = call(api, "/incidents/summary")
        if not quality.get("analysis_pending") and incidents.get("last_run") and quality.get("last_run"):
            break
        time.sleep(1)
    else:
        print("The background pipeline has not finished yet; refresh the UI in a moment.")
    print(f"quality findings: {quality['total']:,}; incidents: {incidents['total']:,}")
    print("Open the UI: Dashboard -> a priority incident -> a timeline event (original record) -> Briefings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
