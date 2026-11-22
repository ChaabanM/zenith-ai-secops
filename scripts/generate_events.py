#!/usr/bin/env python3
"""Generate a deterministic, realistic security-event dataset plus its ground truth.

Examples (run from the repository root):

    python scripts/generate_events.py --count 1000 --seed 42
    python scripts/generate_events.py --count 50000 --seed 42
    python scripts/generate_events.py --count 50000 --seed 42 --profile clean
    python scripts/generate_events.py --count 600 --format json \
        --output data/samples/demo_events.json --ground-truth data/samples/demo_ground_truth.json

The same arguments always produce byte-identical files. The ground-truth file is
for evaluation only and must never be loaded into the platform.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from datagen.generator import Config, generate  # noqa: E402
from datagen.integrity import check_ground_truth  # noqa: E402

JSON_ARRAY_LIMIT = 10_000  # JSON arrays are for small datasets; large sets use JSONL


def _rate(value: str) -> float:
    rate = float(value)
    if not 0.0 <= rate <= 0.5:
        raise argparse.ArgumentTypeError("rate must be between 0 and 0.5")
    return rate


def _start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, required=True, help="exact number of submissions (lines) to generate")
    p.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    p.add_argument("--profile", choices=["mixed", "clean"], default="mixed",
                   help="mixed: realistic defects; clean: no invalid records or duplicate submissions")
    p.add_argument("--format", choices=["jsonl", "json"], default="jsonl", help="jsonl (default) or a JSON array (small sets)")
    p.add_argument("--output", type=Path, help="data file (default data/generated/events_<count>[_clean].<ext>)")
    p.add_argument("--ground-truth", type=Path, help="ground-truth file (default data/generated/ground_truth_<count>[_clean].json)")
    p.add_argument("--start", type=_start, default=None, help="UTC start of the event window (default 2026-01-12T00:00:00Z)")
    p.add_argument("--days", type=float, default=3.0, help="length of the event window in days (default 3)")
    p.add_argument("--invalid-rate", type=_rate, default=0.03, help="share of invalid/malformed submissions (mixed only)")
    p.add_argument("--duplicate-rate", type=_rate, default=0.02, help="share of duplicate submissions (mixed only)")
    p.add_argument("--delay-rate", type=_rate, default=0.02, help="share of delayed / out-of-order events")
    p.add_argument("--low-confidence-rate", type=_rate, default=0.02, help="share of low-confidence detections")
    p.add_argument("--scenario-scale", type=float, default=1.0, help="multiplier for correlated scenario volume")
    p.add_argument("--no-verify", action="store_true", help="skip the ground-truth integrity check")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 2
    if args.format == "json" and args.count > JSON_ARRAY_LIMIT:
        print(f"--format json is limited to {JSON_ARRAY_LIMIT} records; use jsonl", file=sys.stderr)
        return 2
    if args.days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 2

    suffix = "" if args.profile == "mixed" else f"_{args.profile}"
    out = args.output or REPO_ROOT / "data" / "generated" / f"events_{args.count}{suffix}.{args.format}"
    truth_path = args.ground_truth or REPO_ROOT / "data" / "generated" / f"ground_truth_{args.count}{suffix}.json"

    config = Config(
        count=args.count,
        seed=args.seed,
        days=args.days,
        profile=args.profile,
        invalid_rate=args.invalid_rate,
        duplicate_rate=args.duplicate_rate,
        delay_rate=args.delay_rate,
        low_confidence_rate=args.low_confidence_rate,
        scenario_scale=args.scenario_scale,
    )
    if args.start is not None:
        config.start = args.start

    started = time.perf_counter()
    dataset = generate(config)
    generated = time.perf_counter()
    if args.format == "json":
        dataset.write_json(out)
    else:
        dataset.write_jsonl(out)
    dataset.write_ground_truth(truth_path, _relative(out))
    written = time.perf_counter()

    truth = dataset.ground_truth(_relative(out))
    if not args.no_verify:
        problems = check_ground_truth(dataset.lines(), truth)
        if problems:
            print(f"ground-truth integrity check FAILED ({len(problems)} problems):", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  - {problem}", file=sys.stderr)
            return 1

    report = {
        "data_file": _relative(out),
        "ground_truth_file": _relative(truth_path),
        "expected_outcomes": truth["expected_outcomes"],
        "sources": len(truth["source_counts"]),
        "scenario_counts": truth["scenario_counts"],
        "generation_seconds": round(generated - started, 3),
        "write_seconds": round(written - generated, 3),
        "integrity_check": "skipped" if args.no_verify else "passed",
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
