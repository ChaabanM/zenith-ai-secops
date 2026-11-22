"""Dataset generator: determinism, exact counts, scenario coverage and ground-truth integrity."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.schemas.sources import SourceType
from app.services.ingestion.records import loads_strict, validate_event
from datagen.generator import SCENARIO_TYPES, Config, generate
from datagen.integrity import check_ground_truth

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = REPO_ROOT / "data" / "samples"
CLI = REPO_ROOT / "scripts" / "generate_events.py"


@pytest.fixture(scope="module")
def mixed():
    dataset = generate(Config(count=3000, seed=42))
    return dataset, dataset.ground_truth("events.jsonl")


@pytest.fixture(scope="module")
def mixed_lines(mixed):
    return mixed[0].lines()


def test_same_seed_is_byte_identical():
    a, b = generate(Config(count=800, seed=7)), generate(Config(count=800, seed=7))
    assert a.lines() == b.lines()
    assert json.dumps(a.ground_truth("f")) == json.dumps(b.ground_truth("f"))


def test_different_seed_differs():
    assert generate(Config(count=300, seed=1)).lines() != generate(Config(count=300, seed=2)).lines()


@pytest.mark.parametrize("count", [1, 2, 7, 15, 16, 99, 500, 1234])
@pytest.mark.parametrize("profile", ["mixed", "clean"])
def test_exact_submission_count(count, profile):
    dataset = generate(Config(count=count, seed=3, profile=profile))
    assert len(dataset.lines()) == count
    truth = dataset.ground_truth("f")
    assert truth["expected_outcomes"]["total_submissions"] == count
    assert check_ground_truth(dataset.lines(), truth) == []


def test_rates_are_configurable():
    truth = generate(Config(count=2000, seed=5, invalid_rate=0.1, duplicate_rate=0.05)).ground_truth("f")
    assert truth["expected_outcomes"]["invalid"] == 200
    assert truth["expected_outcomes"]["duplicate"] == 100


def test_ground_truth_integrity(mixed, mixed_lines):
    _, truth = mixed
    assert check_ground_truth(mixed_lines, truth) == []


def test_all_scenario_types_present(mixed):
    _, truth = mixed
    expected = {f"{k}_{v}" for k, v in SCENARIO_TYPES.items()}
    assert set(truth["scenario_counts"]) == expected
    variants = set(truth["scenario_variant_counts"])
    for required in [
        "E:contradictory_vehicle_details", "E:conflicting_identity", "E:conflicting_timestamps", "E:conflicting_locations",
        "E:conflicting_sensor_results", "G:reverse_order_arrival", "G:clock_skew", "G:store_and_forward_delay",
        "J:repeated_device_events", "J:access_failure_spike", "J:event_spike", "J:localized_cluster",
        "K:same_zone_different_subjects", "K:same_time_different_incidents", "K:same_device_separate_observations",
        "K:similar_types_no_relation", "L:missing_entity_identifiers", "L:insufficient_linking_evidence",
        "L:unresolved_conflicting_evidence",
    ]:
        assert required in variants, required
    i_variants = {v for v in variants if v.startswith("I:")}
    assert any("face" in v for v in i_variants) and any("cctv" in v for v in i_variants) and any("anpr" in v for v in i_variants)
    anomalies = truth["anomaly_counts"]
    for kind in ["duplicate_exact", "duplicate_conflicting", "invalid_record", "device_offline", "device_recovered",
                 "sensor_failure", "device_degraded", "expected_absence", "low_confidence_detection", "out_of_order_arrival",
                 "delayed_event", "timestamp_inconsistency"]:
        assert anomalies.get(kind, 0) > 0, kind


def test_all_fifteen_sources_are_generated_and_valid(mixed, mixed_lines):
    _, truth = mixed
    assert set(truth["source_counts"]) == {s.value for s in SourceType}
    # every line expected to be accepted passes the production validator
    for entry, line in zip(truth["submissions"], mixed_lines, strict=True):
        if entry["expected_status"] == "invalid":
            continue
        event, errors, _ = validate_event(loads_strict(line))
        assert errors == [] and event is not None, (entry["line"], errors)


def test_invalid_lines_are_rejected_by_the_validator(mixed, mixed_lines):
    _, truth = mixed
    reasons = set()
    for entry, line in zip(truth["submissions"], mixed_lines, strict=True):
        if entry["expected_status"] != "invalid":
            continue
        reasons.update(entry["invalid_reasons"])
        try:
            obj = loads_strict(line)
        except ValueError:
            continue  # malformed JSON line
        event, errors, _ = validate_event(obj)
        assert event is None and errors, entry
    for required in ["missing_event_id", "wrong_type_device_id", "invalid_timestamp_format", "invalid_latitude",
                     "confidence_above_one", "malformed_json_truncated", "malformed_json_legacy_line"]:
        assert required in reasons


def test_clean_profile_has_no_defects():
    truth = generate(Config(count=3000, seed=42, profile="clean")).ground_truth("f")
    outcomes = truth["expected_outcomes"]
    assert outcomes["accepted"] == 3000 and outcomes["invalid"] == outcomes["duplicate"] == 0
    assert "F_duplicate_events" not in truth["scenario_counts"] and "H_malformed_incomplete" not in truth["scenario_counts"]


def test_unrelated_events_must_remain_separate(mixed):
    _, truth = mixed
    k = [s for s in truth["scenarios"] if s["type"] == "K_unrelated_events"]
    assert k and all(s["expected_incident"] == "separate" for s in k)
    separate_ids = {s["scenario_id"] for s in truth["must_remain_separate"]}
    assert all(s["scenario_id"] in separate_ids for s in k)
    for sep in truth["must_remain_separate"]:
        groups = [set(g) for g in sep["groups"]]
        assert len(groups) >= 2 and all(groups)
        assert sum(len(g) for g in groups) == len(set().union(*groups))
    l_scenarios = [s for s in truth["scenarios"] if s["type"] == "L_ambiguous_uncorrelatable"]
    assert all(s["expected_incident"] == "uncertain" and s["expected_uncertainty"] for s in l_scenarios)


def test_device_failure_absence_has_no_fabricated_events(mixed, mixed_lines):
    _, truth = mixed
    d = [s for s in truth["scenarios"] if s["type"] == "D_device_failure"]
    assert d and all(s["expected_absences"] for s in d)
    # covered in detail by check_ground_truth; spot-check that health events exist for the device
    for scenario in d:
        device_id = scenario["expected_absences"][0]["device_id"]
        types = {json.loads(mixed_lines[i])["event_type"] for i in scenario["submission_indexes"]}
        assert {"device_degraded", "sensor_failure", "device_offline", "device_recovered"} <= types
        assert all(json.loads(mixed_lines[i])["device_id"] == device_id for i in scenario["submission_indexes"])


def test_events_span_multiple_shifts_and_days(mixed, mixed_lines):
    hours, days = set(), set()
    for line in mixed_lines[:2000]:
        try:
            record = json.loads(line)
            occurred = record["occurred_at"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(occurred, str) and occurred[:4] == "2026" and "T" in occurred:
            days.add(occurred[:10])
            hours.add(int(occurred[11:13]))
    assert len(days) >= 3 and len(hours) >= 20


def test_no_label_leakage(mixed_lines):
    for line in mixed_lines:
        assert "SCN-" not in line and "scenario" not in line and "expected" not in line


def test_integrity_checker_detects_corruption(mixed, mixed_lines):
    _, truth = mixed
    broken = copy.deepcopy(truth)
    broken["scenarios"][0]["related_event_ids"].append("evt-does-not-exist")
    broken["expected_outcomes"]["accepted"] += 1
    problems = check_ground_truth(mixed_lines, broken)
    assert any("evt-does-not-exist" in p for p in problems)
    assert any("expected_outcomes" in p for p in problems)

    leaked = list(mixed_lines)
    record = json.loads(leaked[0])
    record["payload"]["scenario_id"] = "SCN-A-0001"
    leaked[0] = json.dumps(record)
    assert any("label" in p or "marker" in p for p in check_ground_truth(leaked, truth))

    assert check_ground_truth(mixed_lines[:-1], truth)  # missing line


def test_committed_demo_dataset_is_reproducible(tmp_path):
    out, gt = tmp_path / "demo.json", tmp_path / "gt.json"
    result = subprocess.run(
        [sys.executable, str(CLI), "--count", "500", "--seed", "42", "--format", "json", "--output", str(out), "--ground-truth", str(gt)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout)["integrity_check"] == "passed"
    assert out.read_bytes() == (SAMPLES / "demo_events.json").read_bytes()
    regenerated = json.loads(gt.read_text())
    committed = json.loads((SAMPLES / "demo_ground_truth.json").read_text())
    regenerated.pop("data_file"), committed.pop("data_file")
    assert regenerated == committed


def test_demo_dataset_covers_every_source_and_scenario():
    truth = json.loads((SAMPLES / "demo_ground_truth.json").read_text())
    events = json.loads((SAMPLES / "demo_events.json").read_text())
    assert len(events) == truth["expected_outcomes"]["total_submissions"] == 500
    assert set(truth["source_counts"]) == {s.value for s in SourceType}
    assert set(truth["scenario_counts"]) == {f"{k}_{v}" for k, v in SCENARIO_TYPES.items()}
    lines = [e if isinstance(e, str) else json.dumps(e, separators=(",", ":")) for e in events]
    assert check_ground_truth(lines, truth) == []


def test_cli_writes_jsonl_and_rejects_bad_arguments(tmp_path):
    out, gt = tmp_path / "e.jsonl", tmp_path / "g.json"
    subprocess.run([sys.executable, str(CLI), "--count", "40", "--seed", "1", "--output", str(out), "--ground-truth", str(gt)],
                   capture_output=True, check=True)
    assert len(out.read_text().splitlines()) == 40
    assert json.loads(gt.read_text())["generator"]["seed"] == 1
    bad = subprocess.run([sys.executable, str(CLI), "--count", "0"], capture_output=True)
    assert bad.returncode != 0
    too_big = subprocess.run([sys.executable, str(CLI), "--count", "20000", "--format", "json", "--output", str(out)], capture_output=True)
    assert too_big.returncode != 0
