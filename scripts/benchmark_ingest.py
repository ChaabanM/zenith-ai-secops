#!/usr/bin/env python3
"""Performance tests: clean and mixed 50,000-submission ingestion + data-quality analysis.

For each test (``clean`` and ``mixed``) this script:

1. generates the dataset with ``scripts/generate_events.py`` (separate process,
   timed, peak memory measured);
2. starts the real API (``uvicorn app.main:app``) on a fresh SQLite database;
3. streams the JSONL file to ``POST /ingest`` over HTTP (bounded chunks; no
   direct SQL inserts, so every record goes through validation);
4. verifies the summary *and every individual record outcome* against the
   ground truth, traverses every page of ``GET /events`` and checks for losses,
   duplicates and ordering;
5. measures pagination / filter / detail latency, database size and the peak
   memory of the server process;
6. (Phase 3) runs the data-quality analysis through ``POST /quality-issues/analyze``,
   re-runs it to prove idempotency, checks every evidence reference, measures
   issue-query latency and storage, and compares findings with the ground truth;
7. (Phase 4A) runs ``POST /incidents/correlate`` twice (idempotency), measures
   incident-query latency, checks incident evidence links and that source events
   were not modified, and compares incidents with the ground truth;
8. (Phase 5) generates ``GET /briefing`` for every day and every shift of the
   dataset, measures latency, checks each period's event and incident counts
   against direct SQL, and checks that every referenced event / incident /
   quality issue exists; then times ``GET /dashboard/summary`` (default shift and
   latest day) and checks its counts against SQL;
9. (mixed test) measures ingestion again with the background pipeline enabled
   (quality analysis + correlation: the normal operator workflow).

Usage (repository root):

    backend/.venv/bin/python scripts/benchmark_ingest.py            # both tests, 50,000 each
    backend/.venv/bin/python scripts/benchmark_ingest.py --count 5000 --tests mixed

Results are written to data/generated/benchmark_results.json (not committed).
Exit code is non-zero when any verification fails.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from datagen.correlation_eval import evaluate as evaluate_correlation  # noqa: E402
from datagen.quality_eval import evaluate as evaluate_quality  # noqa: E402
GENERATED = REPO_ROOT / "data" / "generated"
PY = sys.executable


def maxrss_mb(rusage: Any) -> float:
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    scale = 1 if sys.platform == "darwin" else 1024
    return round(rusage.ru_maxrss * scale / (1024 * 1024), 1)


def run_measured(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str, float, float]:
    """Run a child process; return (exit code, stdout, seconds, peak RSS MB)."""
    started = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out_chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:  # drain before waiting so the pipe never blocks
        out_chunks.append(line)
    _, status, rusage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    err = proc.stderr.read() if proc.stderr else ""
    if proc.returncode:
        print(err, file=sys.stderr)
    return proc.returncode, "".join(out_chunks), time.perf_counter() - started, maxrss_mb(rusage)


def environment() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "cpu_count": os.cpu_count(),
    }
    if sys.platform == "darwin":
        def sysctl(name: str) -> str:
            return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()

        info["cpu"] = sysctl("machdep.cpu.brand_string")
        mem = sysctl("hw.memsize")
        info["memory_gb"] = round(int(mem) / 1024**3, 1) if mem.isdigit() else None
    else:
        info["cpu"] = platform.processor()
        try:
            with open("/proc/meminfo") as fh:
                info["memory_gb"] = round(int(fh.readline().split()[1]) / 1024**2, 1)
        except OSError:
            info["memory_gb"] = None
    try:
        import fastapi
        import pydantic
        import sqlalchemy
        import uvicorn

        info["packages"] = {"fastapi": fastapi.__version__, "pydantic": pydantic.__version__,
                            "sqlalchemy": sqlalchemy.__version__, "uvicorn": uvicorn.__version__, "httpx": httpx.__version__}
    except ImportError:
        pass
    return info


class Server:
    def __init__(self, db_path: Path, port: int, auto_analyze: bool = False):
        self.db_path = db_path
        self.port = port
        self.auto_analyze = auto_analyze
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
        env = {**os.environ, "ZENITH_DATABASE_URL": f"sqlite:///{self.db_path}", "ZENITH_ENVIRONMENT": "benchmark",
               "ZENITH_QUALITY_AUTO_ANALYZE": "true" if self.auto_analyze else "false"}
        self.proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.base}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError("server exited during startup")
            time.sleep(0.2)
        raise RuntimeError("server did not become healthy")

    def stop(self) -> float:
        """Stop gracefully and return the server's peak RSS in MB."""
        assert self.proc is not None
        self.proc.send_signal(signal.SIGINT)
        _, status, rusage = os.wait4(self.proc.pid, 0)
        self.proc.returncode = os.waitstatus_to_exitcode(status)
        return maxrss_mb(rusage)


def file_chunks(path: Path, size: int = 1 << 20):
    with path.open("rb") as fh:
        while chunk := fh.read(size):
            yield chunk


def timed_get(client: httpx.Client, path: str, params: dict[str, Any] | None = None, runs: int = 5) -> tuple[dict[str, Any], Any]:
    client.get(path, params=params)  # warm-up
    samples = []
    body = None
    for _ in range(runs):
        t = time.perf_counter()
        resp = client.get(path, params=params)
        samples.append((time.perf_counter() - t) * 1000)
        resp.raise_for_status()
        body = resp.json()
    return {"median_ms": round(statistics.median(samples), 2), "max_ms": round(max(samples), 2)}, body


def briefing_phase(client: httpx.Client, db_path: Path, problems: list[str]) -> dict[str, Any]:
    """Phase 5: briefings for every day and shift; counts and references checked against SQL."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    days = [r[0] for r in con.execute("SELECT DISTINCT substr(occurred_at, 1, 10) FROM events ORDER BY 1")]
    periods = [(f"day {d}", {"mode": "day", "date": d}) for d in days]
    periods += [(f"{d} {s}", {"mode": "shift", "date": d, "shift": s}) for d in days for s in ("day", "evening", "night")]
    periods.append(("default", {}))
    latencies, checked = [], {"periods": 0, "event_refs": 0, "incident_refs": 0, "quality_refs": 0}
    detail: dict[str, Any] = {}
    for label, params in periods:
        stats, b = timed_get(client, "/briefing", params, runs=3)
        latencies.append(stats["median_ms"])
        start = b["period"]["start"].replace("T", " ").replace("Z", "")
        end = b["period"]["end"].replace("T", " ").replace("Z", "")
        events = con.execute("SELECT COUNT(*) FROM events WHERE occurred_at >= ? AND occurred_at < ?", (start, end)).fetchone()[0]
        incidents = con.execute("SELECT COUNT(*) FROM incidents WHERE correlation_state = 'active' AND last_occurred_at >= ? "
                                "AND first_occurred_at < ?", (start, end)).fetchone()[0]
        if b["key_metrics"]["events_total"] != events:
            problems.append(f"briefing {label}: events_total {b['key_metrics']['events_total']} != SQL {events}")
        if b["key_metrics"]["incidents_active"] != incidents:
            problems.append(f"briefing {label}: incidents_active {b['key_metrics']['incidents_active']} != SQL {incidents}")
        if not b["grounding"]["all_references_verified"]:
            problems.append(f"briefing {label}: unverified references {b['grounding']['unverified_references'][:5]}")
        for ref in b["evidence_index"]:
            row = con.execute("SELECT event_id FROM events WHERE id = ?", (ref["event_pk"],)).fetchone()
            if row is None or row[0] != ref["event_id"]:
                problems.append(f"briefing {label}: bad event reference {ref}")
        for iid in b["incident_ids"]:
            if con.execute("SELECT 1 FROM incidents WHERE id = ?", (iid,)).fetchone() is None:
                problems.append(f"briefing {label}: missing incident {iid}")
        for qid in b["quality_issue_ids"]:
            if con.execute("SELECT 1 FROM quality_issues WHERE id = ?", (qid,)).fetchone() is None:
                problems.append(f"briefing {label}: missing quality issue {qid}")
        checked["periods"] += 1
        checked["event_refs"] += len(b["evidence_index"])
        checked["incident_refs"] += len(b["incident_ids"])
        checked["quality_refs"] += len(b["quality_issue_ids"])
        if params.get("mode") == "day" or label == "default":
            detail[label] = {**stats, "events": events, "incidents": incidents, "priority": len(b["priority_incidents"]),
                             "findings": sum(len(s["findings"]) for s in b["sections"]), "actions": len(b["recommended_actions"]),
                             "confidence": f"{b['confidence']['level']} ({b['confidence']['score']})", "period": b["period"]["label"]}
    # Phase 6: GET /dashboard/summary (default shift and latest day): latency, counts vs SQL, grounding
    dashboard: dict[str, Any] = {}
    for label, params in (("default", {}), ("day", {"mode": "day"})):
        stats, dsum = timed_get(client, "/dashboard/summary", params, runs=5)
        start = dsum["period"]["start"].replace("T", " ").replace("Z", "")
        end = dsum["period"]["end"].replace("T", " ").replace("Z", "")
        events = con.execute("SELECT COUNT(*) FROM events WHERE occurred_at >= ? AND occurred_at < ?", (start, end)).fetchone()[0]
        stored = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if dsum["period_metrics"]["events_total"] != events or dsum["stored_totals"]["events_stored"] != stored:
            problems.append(f"dashboard {label}: counts differ from SQL")
        if not dsum["grounding"]["all_references_verified"]:
            problems.append(f"dashboard {label}: unverified references")
        dashboard[label] = {**stats, "events": events, "period": dsum["period"]["label"]}
    con.close()
    print(f"dashboard: default {dashboard['default']['median_ms']:.0f} ms, day {dashboard['day']['median_ms']:.0f} ms (median of 5)", flush=True)
    print(f"briefings: {checked['periods']} periods, median {statistics.median(latencies):.0f} ms, max {max(latencies):.0f} ms; "
          f"{checked['event_refs']} event / {checked['incident_refs']} incident / {checked['quality_refs']} quality references checked", flush=True)
    return {"latency_median_ms": round(statistics.median(latencies), 1), "latency_max_ms": round(max(latencies), 1),
            "checked": checked, "by_period": detail, "dashboard": dashboard}


def verify_records(db_path: Path, batch_id: str, truth: dict[str, Any]) -> list[str]:
    """Compare every stored submission with its ground-truth expectation."""
    problems: list[str] = []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT r.position, r.status, r.duplicate_kind, r.external_event_id, r.normalized_event_id, e.event_id, s2.position "
        "FROM raw_submissions r LEFT JOIN events e ON e.id = r.normalized_event_id "
        "LEFT JOIN raw_submissions s2 ON s2.id = e.submission_id "
        "WHERE r.batch_id = ? ORDER BY r.position", (batch_id,)
    ).fetchall()
    expected = truth["submissions"]
    if len(rows) != len(expected):
        problems.append(f"{len(rows)} stored submissions vs {len(expected)} expected")
    for row, exp in zip(rows, expected, strict=False):
        position, status, kind, ext_id, norm_id, linked_event_id, origin_position = row
        if position != exp["line"]:
            problems.append(f"position {position} vs expected line {exp['line']}")
            break
        if status != exp["expected_status"]:
            problems.append(f"line {position}: status {status}, expected {exp['expected_status']}")
        elif status == "duplicate":
            if kind != exp["duplicate_kind"]:
                problems.append(f"line {position}: duplicate kind {kind}, expected {exp['duplicate_kind']}")
            if origin_position != exp["duplicate_of_index"] + 1:
                problems.append(f"line {position}: linked to line {origin_position}, expected {exp['duplicate_of_index'] + 1}")
        if status in ("accepted", "duplicate") and (ext_id != exp["event_id"] or linked_event_id != exp["event_id"]):
            problems.append(f"line {position}: event id not preserved ({ext_id} / {linked_event_id} vs {exp['event_id']})")
        if status == "invalid" and norm_id is not None:
            problems.append(f"line {position}: invalid submission linked to an event")
        if len(problems) > 20:
            break
    events = con.execute("SELECT COUNT(*), COUNT(DISTINCT event_id) FROM events").fetchone()
    if events[0] != events[1]:
        problems.append(f"events table holds duplicate event_ids ({events[0]} rows, {events[1]} distinct)")
    con.close()
    return problems


def quality_phase(client: httpx.Client, problems: list[str]) -> dict[str, Any]:
    """Run the analysis twice (idempotency), then measure issue queries."""
    t = time.perf_counter()
    first = client.post("/quality-issues/analyze").json()
    analysis_seconds = time.perf_counter() - t
    second = client.post("/quality-issues/analyze").json()
    if (second["inserted"], second["updated"], second["removed"]) != (0, 0, 0) or second["unchanged"] != first["issues_total"]:
        problems.append(f"re-analysis was not idempotent: {second}")
    summary = client.get("/quality-issues/summary").json()
    if summary["total"] != first["issues_total"]:
        problems.append(f"stored issues {summary['total']} != produced {first['issues_total']}")
    print(f"quality analysis {analysis_seconds:.2f}s: {first['issues_total']} issues; re-run "
          f"inserted={second['inserted']} updated={second['updated']} removed={second['removed']} unchanged={second['unchanged']}", flush=True)

    biggest = client.get("/quality-issues", params={"issue_type": "event_spike", "page_size": 1}).json()["items"]
    any_issue = client.get("/quality-issues", params={"page_size": 1}).json()["items"]
    sample_device = summary["devices"][0]["value"] if summary["devices"] else "none"
    sample_zone = summary["zones"][0]["value"] if summary["zones"] else "none"
    queries: dict[str, tuple[str, dict[str, Any]]] = {
        "list_page_1": ("/quality-issues", {"page_size": 50}),
        "list_page_last": ("/quality-issues", {"page_size": 50, "page": max(1, -(-summary["total"] // 50))}),
        "filter_type": ("/quality-issues", {"issue_type": "delayed_event"}),
        "filter_severity": ("/quality-issues", {"severity": "high,critical"}),
        "filter_device": ("/quality-issues", {"device_id": sample_device}),
        "filter_zone": ("/quality-issues", {"zone": sample_zone}),
        "filter_time_8h": ("/quality-issues", {"occurred_from": "2026-01-13T00:00:00Z", "occurred_to": "2026-01-13T08:00:00Z"}),
        "summary": ("/quality-issues/summary", {}),
    }
    if any_issue:
        queries["detail"] = (f"/quality-issues/{any_issue[0]['id']}", {})
    if biggest:
        queries["detail_largest_spike"] = (f"/quality-issues/{biggest[0]['id']}", {})
    latency = {}
    for label, (path, params) in queries.items():
        stats, result = timed_get(client, path, params)
        if isinstance(result, dict) and "total" in result and label != "summary":
            stats["matching"] = result["total"]
        latency[label] = stats
    return {
        "analysis_seconds_http": round(analysis_seconds, 2),
        "analysis_run": {k: first[k] for k in ("duration_ms", "events_analyzed", "submissions_analyzed", "issues_total")},
        "rule_timings_ms": {k: v["ms"] for k, v in first["details"]["rules"].items()},
        "server_peak_rss_after_analysis_mb": first["details"]["process_peak_rss_mb"],
        "rerun": {k: second[k] for k in ("duration_ms", "inserted", "updated", "unchanged", "removed")},
        "issues_by_type": {x["value"]: x["count"] for x in summary["by_type"]},
        "issues_by_severity": {x["value"]: x["count"] for x in summary["by_severity"]},
        "latency_ms": latency,
    }


def events_fingerprint(db_path: Path) -> tuple:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*), SUM(id), GROUP_CONCAT(content_sha256, '') FROM (SELECT id, content_sha256 FROM events ORDER BY id)").fetchone()
    finally:
        con.close()


def correlation_phase(client: httpx.Client, problems: list[str]) -> dict[str, Any]:
    t = time.perf_counter()
    first = client.post("/incidents/correlate").json()
    seconds = time.perf_counter() - t
    second = client.post("/incidents/correlate").json()
    if (second["created"], second["updated"], second["merged"], second["obsoleted"]) != (0, 0, 0, 0):
        problems.append(f"re-correlation was not idempotent: {second}")
    summary = client.get("/incidents/summary").json()
    print(f"correlation {seconds:.2f}s: {first['incidents_active']} incidents; re-run created={second['created']} "
          f"updated={second['updated']} unchanged={second['unchanged']}", flush=True)
    biggest = client.get("/incidents", params={"category": "cyber_security", "page_size": 1}).json()["items"]
    any_incident = client.get("/incidents", params={"page_size": 1}).json()["items"]
    queries: dict[str, tuple[str, dict[str, Any]]] = {
        "list_page_1": ("/incidents", {"page_size": 50}),
        "filter_category": ("/incidents", {"category": "traffic_enforcement"}),
        "filter_severity": ("/incidents", {"severity": "critical,high"}),
        "filter_zone": ("/incidents", {"zone": "Gate-A"}),
        "filter_device": ("/incidents", {"device_id": "anpr-int01"}),
        "filter_time_8h": ("/incidents", {"occurred_from": "2026-01-13T00:00:00Z", "occurred_to": "2026-01-13T08:00:00Z"}),
        "summary": ("/incidents/summary", {}),
    }
    if any_incident:
        queries["detail"] = (f"/incidents/{any_incident[0]['id']}", {})
    if biggest:
        queries["detail_largest"] = (f"/incidents/{biggest[0]['id']}", {})
    latency = {}
    for label, (path, params) in queries.items():
        stats, result = timed_get(client, path, params)
        if isinstance(result, dict) and "total" in result and label != "summary":
            stats["matching"] = result["total"]
        latency[label] = stats
    return {
        "correlation_seconds_http": round(seconds, 2),
        "run": {k: first[k] for k in ("duration_ms", "observations_loaded", "incidents_active", "created")},
        "timings_ms": first["details"]["timings"],
        "stats": first["details"]["stats"],
        "server_peak_rss_after_correlation_mb": first["details"]["process_peak_rss_mb"],
        "rerun": {k: second[k] for k in ("duration_ms", "created", "updated", "unchanged", "merged", "obsoleted")},
        "by_category": {x["value"]: x["count"] for x in summary["by_category"]},
        "by_severity": {x["value"]: x["count"] for x in summary["by_severity"]},
        "by_confidence": {x["value"]: x["count"] for x in summary["by_confidence"]},
        "latency_ms": latency,
    }


def incident_storage(db_path: Path, problems: list[str]) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    checks = {
        "evidence_rows": "SELECT COUNT(*) FROM incident_evidence",
        "broken_event_refs": "SELECT COUNT(*) FROM incident_evidence l LEFT JOIN events e ON e.id = l.event_pk "
                             "WHERE e.id IS NULL OR e.event_id != l.external_event_id OR e.submission_id != l.submission_id",
        "duplicate_links": "SELECT COUNT(*) - COUNT(DISTINCT incident_id || ':' || event_pk) FROM incident_evidence",
        "events_in_two_active_incidents": "SELECT COUNT(*) FROM (SELECT l.event_pk FROM incident_evidence l JOIN incidents i ON i.id = l.incident_id "
                                          "WHERE i.correlation_state = 'active' GROUP BY l.event_pk HAVING COUNT(*) > 1)",
        "incidents_without_evidence": "SELECT COUNT(*) FROM incidents i WHERE i.correlation_state = 'active' AND NOT EXISTS "
                                      "(SELECT 1 FROM incident_evidence l WHERE l.incident_id = i.id)",
    }
    out = {name: con.execute(sql).fetchone()[0] for name, sql in checks.items()}
    try:
        size = con.execute("SELECT SUM(pgsize) FROM dbstat WHERE name LIKE 'incident%' OR name LIKE 'ix_incident%' "
                           "OR name LIKE 'sqlite_autoindex_incident%' OR name = 'uq_incident_event'").fetchone()[0]
    except sqlite3.OperationalError:
        size = None
    con.close()
    for name in ("broken_event_refs", "duplicate_links", "events_in_two_active_incidents", "incidents_without_evidence"):
        if out[name]:
            problems.append(f"incident storage check {name} = {out[name]}")
    out["incident_storage_mb"] = round(size / 1024**2, 2) if size else None
    return out


def quality_storage(db_path: Path, problems: list[str], submissions: int) -> dict[str, Any]:
    """Issue storage size and evidence-reference integrity (read-only)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        size = con.execute(
            "SELECT SUM(pgsize) FROM dbstat WHERE name LIKE 'quality%' OR name LIKE 'ix_quality%' OR name LIKE 'sqlite_autoindex_quality%'"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        size = None
    checks = {
        "evidence_rows": "SELECT COUNT(*) FROM quality_issue_evidence",
        "broken_event_refs": "SELECT COUNT(*) FROM quality_issue_evidence q LEFT JOIN events e ON e.id = q.event_pk "
                             "WHERE q.event_pk IS NOT NULL AND (e.id IS NULL OR e.event_id != q.external_event_id)",
        "broken_submission_refs": "SELECT COUNT(*) FROM quality_issue_evidence q LEFT JOIN raw_submissions r ON r.id = q.submission_id "
                                  "WHERE q.submission_id IS NOT NULL AND r.id IS NULL",
        "issues_without_evidence": "SELECT COUNT(*) FROM quality_issues i WHERE NOT EXISTS (SELECT 1 FROM quality_issue_evidence q WHERE q.issue_id = i.id)",
        "duplicate_fingerprints": "SELECT COUNT(*) - COUNT(DISTINCT fingerprint) FROM quality_issues",
        "raw_submissions": "SELECT COUNT(*) FROM raw_submissions",
    }
    out = {name: con.execute(sql).fetchone()[0] for name, sql in checks.items()}
    con.close()
    for name in ("broken_event_refs", "broken_submission_refs", "issues_without_evidence", "duplicate_fingerprints"):
        if out[name]:
            problems.append(f"quality storage check {name} = {out[name]}")
    if out["raw_submissions"] != submissions:
        problems.append(f"raw evidence lost: {out['raw_submissions']} submissions stored, {submissions} sent")
    out["issue_storage_mb"] = round(size / 1024**2, 2) if size else None
    return out


def auto_impact(data_file: Path, port: int, expected: dict[str, Any]) -> dict[str, Any]:
    """Ingest again on a fresh DB with background analysis enabled (the normal workflow)."""
    db_path = GENERATED / "benchmark_mixed_auto.db"
    server = Server(db_path, port, auto_analyze=True)
    server.start()
    try:
        with httpx.Client(base_url=server.base, timeout=None) as client:
            started = time.perf_counter()
            resp = client.post("/ingest", content=file_chunks(data_file), headers={"Content-Type": "application/x-ndjson"})
            ingest_seconds = time.perf_counter() - started
            body = resp.json()
            while True:
                summary = client.get("/quality-issues/summary").json()
                if not summary["analysis_pending"] and summary["last_run"]:
                    break
                time.sleep(0.2)
            ready_seconds = time.perf_counter() - started
            incidents = client.get("/incidents/summary").json()
    finally:
        rss = server.stop()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    result = {
        "ingest_seconds": round(ingest_seconds, 2),
        "accepted": body["accepted"],
        "quality_analysis": body["quality_analysis"],
        "issues_and_incidents_queryable_after_seconds": round(ready_seconds, 2),
        "incidents": incidents["total"],
        "background_correlation_ms": incidents["last_run"]["duration_ms"] if incidents["last_run"] else None,
        "background_run_ms": summary["last_run"]["duration_ms"],
        "issues": summary["total"],
        "server_peak_rss_mb": rss,
    }
    print(f"with background pipeline: ingest {ingest_seconds:.2f}s, issues + incidents queryable after {ready_seconds:.2f}s "
          f"({summary['total']} issues, {incidents['total']} incidents)", flush=True)
    if body["accepted"] != expected["accepted"]:
        raise SystemExit("auto-analysis ingest produced different counts")
    return result


def run_test(name: str, args: argparse.Namespace) -> dict[str, Any]:
    profile = name
    suffix = "" if profile == "mixed" else "_clean"
    data_file = GENERATED / f"events_{args.count}{suffix}.jsonl"
    truth_file = GENERATED / f"ground_truth_{args.count}{suffix}.json"
    print(f"\n=== Test {'A (clean)' if profile == 'clean' else 'B (mixed)'}: {args.count} submissions ===", flush=True)

    code, out, gen_wall, gen_rss = run_measured(
        [PY, "scripts/generate_events.py", "--count", str(args.count), "--seed", str(args.seed), "--profile", profile,
         "--output", str(data_file), "--ground-truth", str(truth_file)],
        cwd=REPO_ROOT,
    )
    if code:
        raise SystemExit(f"generation failed ({code})")
    gen_report = json.loads(out)
    truth = json.loads(truth_file.read_text())
    expected = truth["expected_outcomes"]
    print(f"generated in {gen_report['generation_seconds']}s (process {gen_wall:.2f}s, peak {gen_rss} MB): {expected}", flush=True)

    db_path = GENERATED / f"benchmark_{profile}.db"
    server = Server(db_path, args.port)
    server.start()
    problems: list[str] = []
    latency: dict[str, Any] = {}
    try:
        with httpx.Client(base_url=server.base, timeout=None) as client:
            started = time.perf_counter()
            resp = client.post("/ingest", params={"filename": data_file.name}, content=file_chunks(data_file),
                               headers={"Content-Type": "application/x-ndjson"})
            ingest_seconds = time.perf_counter() - started
            if resp.status_code not in (200, 201):
                raise SystemExit(f"ingest failed: HTTP {resp.status_code} {resp.text[:500]}")
            summary = resp.json()
            observed = {
                "total_submissions": summary["total_received"],
                "accepted": summary["accepted"],
                "invalid": summary["invalid"],
                "duplicate": summary["duplicate_event_ids"],
                "exact_duplicate": summary["exact_duplicates"],
                "conflicting_duplicate": summary["conflicting_duplicates"],
            }
            if observed != expected:
                problems.append(f"summary {observed} != expected {expected}")
            print(f"ingested in {ingest_seconds:.2f}s ({args.count / ingest_seconds:,.0f} submissions/s): {observed}", flush=True)

            # full traversal of the default listing: no losses, no repeats, stable order
            t = time.perf_counter()
            seen: set[int] = set()
            last_key = None
            page = 1
            while True:
                body = client.get("/events", params={"page": page, "page_size": 500}).json()
                for item in body["items"]:
                    key = (datetime.fromisoformat(item["occurred_at"].replace("Z", "+00:00")), item["id"])
                    if last_key is not None and key > last_key:
                        problems.append(f"ordering violated at event {item['id']}")
                    last_key = key
                    if item["id"] in seen:
                        problems.append(f"event {item['id']} returned twice during traversal")
                    seen.add(item["id"])
                if not body["has_next"]:
                    break
                page += 1
            traversal_seconds = time.perf_counter() - t
            if len(seen) != expected["accepted"]:
                problems.append(f"traversal returned {len(seen)} events, expected {expected['accepted']}")

            total = body["total"]
            facets_latency, facets = timed_get(client, "/events/facets")
            start = datetime.fromisoformat(facets["occurred_min"].replace("Z", "+00:00")).astimezone(UTC)
            window_from = start + timedelta(days=1)
            window_to = window_from + timedelta(hours=8)
            some_event = next(iter(seen))
            queries: dict[str, tuple[str, dict[str, Any]]] = {
                "page_1": ("/events", {"page": 1, "page_size": 50}),
                "page_middle": ("/events", {"page": max(1, total // 100), "page_size": 50}),
                "page_last": ("/events", {"page": max(1, -(-total // 50)), "page_size": 50}),
                "filter_severity_critical": ("/events", {"severity": "critical"}),
                "filter_source_anpr": ("/events", {"source_type": "anpr"}),
                "filter_device": ("/events", {"device_id": "acs-gate-b"}),
                "filter_zone": ("/events", {"zone": "Gate-A"}),
                "filter_time_8h": ("/events", {"occurred_from": window_from.isoformat(), "occurred_to": window_to.isoformat()}),
                "filter_combined": ("/events", {"source_type": "access_control", "severity": "low,medium", "zone": "Gate-B",
                                                "occurred_from": start.isoformat(), "occurred_to": (start + timedelta(days=2)).isoformat()}),
                "event_detail": (f"/events/{some_event}", {}),
            }
            for label, (path, params) in queries.items():
                stats, result = timed_get(client, path, params)
                if "total" in result:
                    stats["matching"] = result["total"]
                latency[label] = stats
            latency["facets"] = facets_latency
            batch_id = summary["batch_id"]
            quality = quality_phase(client, problems)
            before = events_fingerprint(db_path)
            correlation = correlation_phase(client, problems)
            if events_fingerprint(db_path) != before:
                problems.append("source events changed during correlation")
            briefing = briefing_phase(client, db_path, problems)
            if events_fingerprint(db_path) != before:
                problems.append("source events changed during briefing generation")
    finally:
        server_rss = server.stop()

    problems += verify_records(db_path, batch_id, truth)
    quality.update(quality_storage(db_path, problems, expected["total_submissions"]))
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    quality["ground_truth"] = evaluate_quality(con, truth, batch_id)["by_type"]
    correlation["ground_truth"] = evaluate_correlation(con, truth)
    con.close()
    correlation.update(incident_storage(db_path, problems))
    if profile == "mixed" and args.auto_impact:
        quality["ingest_with_background_analysis"] = auto_impact(data_file, args.port, expected)
    db_bytes = sum(Path(f"{db_path}{s}").stat().st_size for s in ("", "-wal", "-shm") if Path(f"{db_path}{s}").exists())
    result = {
        "test": "A_clean" if profile == "clean" else "B_mixed",
        "count": args.count,
        "seed": args.seed,
        "expected_outcomes": expected,
        "observed_outcomes": observed,
        "generation_seconds": gen_report["generation_seconds"],
        "generation_process_seconds": round(gen_wall, 2),
        "generation_peak_rss_mb": gen_rss,
        "ingest_seconds": round(ingest_seconds, 2),
        "submissions_per_second": round(args.count / ingest_seconds),
        "accepted_events_per_second": round(expected["accepted"] / ingest_seconds),
        "server_peak_rss_mb": server_rss,
        "database_mb": round(db_bytes / 1024**2, 1),
        "jsonl_mb": round(data_file.stat().st_size / 1024**2, 1),
        "full_traversal_seconds": round(traversal_seconds, 2),
        "latency_ms": latency,
        "quality": quality,
        "correlation": correlation,
        "briefing": briefing,
        "verification": "passed" if not problems else "FAILED",
        "problems": problems[:50],
    }
    print(f"verification: {result['verification']}  server peak {server_rss} MB  db {result['database_mb']} MB", flush=True)
    for problem in problems[:10]:
        print(f"  - {problem}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tests", default="clean,mixed", help="comma-separated: clean, mixed")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-auto-impact", dest="auto_impact", action="store_false",
                        help="skip the extra mixed ingest with background analysis enabled")
    parser.add_argument("--output", type=Path, default=GENERATED / "benchmark_results.json")
    args = parser.parse_args()

    GENERATED.mkdir(parents=True, exist_ok=True)
    results = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "environment": environment(), "tests": []}
    for name in [t.strip() for t in args.tests.split(",") if t.strip()]:
        if name not in ("clean", "mixed"):
            parser.error(f"unknown test {name}")
        results["tests"].append(run_test(name, args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nresults written to {args.output.relative_to(REPO_ROOT) if args.output.is_relative_to(REPO_ROOT) else args.output}")
    return 0 if all(t["verification"] == "passed" for t in results["tests"]) else 1


if __name__ == "__main__":
    sys.exit(main())
