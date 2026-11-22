#!/usr/bin/env python3
"""Ingest a dataset into a temporary database, run the data-quality analysis through
the API, and compare the findings with the dataset's ground truth.

Examples (repository root):

    backend/.venv/bin/python scripts/evaluate_quality.py                        # committed demo dataset
    backend/.venv/bin/python scripts/evaluate_quality.py \
        --data data/generated/events_50000.jsonl --truth data/generated/ground_truth_50000.json

Prints a precision / recall table per issue type (see docs/data-quality.md for how
to read it). The ground truth is only read here, never by the platform.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data/samples/demo_events.json")
    parser.add_argument("--truth", type=Path, default=REPO_ROOT / "data/samples/demo_ground_truth.json")
    parser.add_argument("--json", type=Path, help="also write the report as JSON")
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="zenith-quality-eval-")
    db_path = Path(workdir) / "eval.db"
    os.environ["ZENITH_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["ZENITH_QUALITY_AUTO_ANALYZE"] = "false"
    os.chdir(REPO_ROOT / "backend")
    sys.path.insert(0, str(REPO_ROOT / "backend"))

    from fastapi.testclient import TestClient

    from app.main import app
    from datagen.quality_eval import evaluate, format_table

    jsonl = args.data.suffix in (".jsonl", ".ndjson")
    with TestClient(app) as client:
        t = time.perf_counter()
        response = client.post("/ingest", content=args.data.read_bytes(),
                               headers={"Content-Type": "application/x-ndjson" if jsonl else "application/json"})
        if response.status_code not in (200, 201):
            print(f"ingest failed: {response.status_code} {response.text[:300]}", file=sys.stderr)
            return 1
        batch_id = response.json()["batch_id"]
        ingest_s = time.perf_counter() - t
        run = client.post("/quality-issues/analyze").json()
    report = evaluate(sqlite3.connect(db_path), json.loads(args.truth.read_text()), batch_id)
    print(f"{args.data.name}: ingested in {ingest_s:.2f}s, analysis {run['duration_ms'] / 1000:.2f}s, {run['issues_total']} issues\n")
    print(format_table(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
