# Dataset generator and ground truth

Code: [`backend/datagen/`](../backend/datagen/) (`world.py` fictional environment, `generator.py` scenarios, `integrity.py` checks) and the CLI [`scripts/generate_events.py`](../scripts/generate_events.py). The generator is evaluation tooling. The application never imports it.

## Commands (repository root)

```bash
backend/.venv/bin/python scripts/generate_events.py --count 1000 --seed 42
backend/.venv/bin/python scripts/generate_events.py --count 50000 --seed 42                  # mixed (default)
backend/.venv/bin/python scripts/generate_events.py --count 50000 --seed 42 --profile clean  # no invalid/duplicate records
backend/.venv/bin/python scripts/generate_events.py --count 500 --seed 42 --format json \
    --output data/samples/demo_events.json --ground-truth data/samples/demo_ground_truth.json  # committed demo
```

| Option | Default | Meaning |
|---|---|---|
| `--count` | required | Exact number of submissions (lines) |
| `--seed` | 42 | Random seed. Same arguments give byte-identical files. |
| `--profile` | `mixed` | `clean` sets the invalid and duplicate rates to 0 |
| `--format` | `jsonl` | `json` writes an array (limited to 10,000 records) |
| `--output`, `--ground-truth` | `data/generated/events_<n>[_clean].jsonl`, `ground_truth_<n>[_clean].json` | Output paths |
| `--start`, `--days` | `2026-01-12T00:00:00Z`, 3 | Event time window: several days and every shift, with fewer events at night |
| `--invalid-rate`, `--duplicate-rate`, `--delay-rate`, `--low-confidence-rate` | 0.03, 0.02, 0.02, 0.02 | Anomaly rates |
| `--scenario-scale` | 1.0 | Multiplier for correlated scenarios (per 1,000 submissions) |

After writing, the CLI runs the ground-truth integrity check and fails if it finds a problem. `data/generated/` is git-ignored.

## Determinism

All randomness comes from a single `random.Random(seed)`. The generator does not read the clock, iterate over sets or use `hash()`. Tests regenerate the committed demo files and compare them byte-for-byte.

## Fictional environment

The setting is an invented city grid around 25.2°N 55.3°E:

- 6 monitored intersections and 2 ANPR checkpoints, each with ANPR, radar, an enforcement camera (red-light / speeding, mobile-phone and seatbelt analytics) and CCTV.
- A secure campus with gates A–C, a data hall, three fence lines, a car park and a control room, covered by access control, face recognition, perimeter sensors, alarm panels and operator consoles.
- A patrol fleet of 12 GPS units, network/security sensors (firewall, IDS, VPN, domain authentication) and 3 weather stations.

Plates, credentials, employees (`EMP-…`), watch-list subjects (`SUBJ-W…`), operators and vehicles are synthetic. IP addresses use RFC 5737 documentation ranges and private space.

**Identifier plausibility.** A sensor only carries identifiers it could know. Radar reports a track id and speed, never a plate. Generic CCTV reports a camera-local track id and appearance, never a plate or identity. A plate appears on ANPR and on the enforcement camera that read it. The enforcement camera may reference the radar track that triggered it. Face recognition reports a gallery subject id or `null`.

Some firmware sends aliases (`red_light_violation`, `lpr`, `network_security_log`), and access-control panels report local `+04:00` timestamps. Both exercise normalization.

## Scenarios (A–L)

The volume scales with `--count`. In the 50K mixed dataset:

| Scenario | Count | What it contains |
|---|---|---|
| A multi-sensor traffic incident | 150 (100 speeding, 50 red-light) | radar → violation camera (same radar track id + plate) → phone/seatbelt detection → ANPR → CCTV within seconds; sometimes a downstream ANPR read minutes later; plus an unrelated bystander plate read at the same site (must stay separate) |
| B unauthorized access | 100 | 3–5 denied badge attempts with one credential (revoked / unknown / schedule / PIN), CCTV loitering, sometimes face no-match and door forced, operator report |
| C perimeter intrusion | 50 | fence cut / climb / beam break → CCTV person → alarm panel → operator report (confirmed or not) |
| D device failure | 50 | degradation (health + low-quality output) → sensor failure → offline → recovery. The outage is an **expected absence** in the ground truth; no events are invented for it. |
| E conflicting sensors | 250 (50 each) | contradictory vehicle details for one plate, badge holder ≠ recognised face, radar vs camera timestamps minutes apart, GPS vs ANPR location conflict, contradictory sensor results. Each has a control observation that is *not* a conflict. |
| F duplicate events | 764 groups, 1,000 duplicate submissions | exact re-transmissions and re-sent copies with changed values (some arriving *before* the original) |
| G delayed / out-of-order | 168 | store-and-forward uploads hours late, GPS trails delivered in reverse order, device clocks in the future (received before occurred); late operator entries are a fallback when the budget is small |
| H malformed / incomplete | 1,500 invalid records | 21 defect types: each missing mandatory field, wrong types, invalid/offset-less timestamps, bad coordinates/confidence/severity, unknown source, non-object payload, truncated JSON, legacy pipe-delimited lines, non-objects, `{}` |
| I low confidence | 736 | uncertain face matches, low-confidence CCTV, unreadable or partial ANPR reads |
| J repeated events and spikes | 50 | one device repeating an event, access-failure bursts, sudden event spikes, localized clusters |
| K unrelated events | 100 | same zone with different subjects, same time with different incidents, same device with separate observations, similar types with no relation. All are listed under `must_remain_separate`. |
| L ambiguous | 100 | missing entity identifiers, insufficient linking evidence, unresolved conflicting evidence, missing location (`expected_incident: "uncertain"`) |

Ordinary background activity fills the remaining budget for all 15 sources. 50K mixed accepted events by source:

| Source | Events | Source | Events | Source | Events |
|---|---|---|---|---|---|
| anpr | 9,065 | network_security | 3,761 | mobile_phone_detection | 1,340 |
| access_control | 7,032 | radar | 3,279 | seatbelt_detection | 1,314 |
| cctv | 6,277 | traffic_violation | 2,714 | perimeter_intrusion | 1,306 |
| gps_fleet | 4,288 | device_health | 2,428 | operator_report | 1,010 |
| face_recognition | 2,386 | weather_environmental | 852 | emergency_alarm | 448 |

Expected outcomes (50K mixed): 47,500 accepted, 1,500 invalid, 1,000 duplicates (476 exact, 524 conflicting).

The demo dataset (`data/samples/demo_events.json`, 500 submissions) covers all 15 sources and all 12 scenario types: 475 accepted, 15 invalid, 10 duplicates (5 exact, 5 conflicting).

### Generator 1.1.0 realism changes (Phase 3)

Phase 3 judges "ordinary movement" and "different people at the same gate", so the background data has to behave physically. Version 1.1.0 changes four things, and the committed demo files were regenerated with it:

- **Local background traffic.** Each road site has its own pool of regular vehicles, so ordinary plates never jump between distant sites. Cross-site movement happens only in scenarios.
- **Realistic travel.** Scenario A's downstream ANPR read happens after a realistic travel time for the distance (50–90 km/h). Fleet vehicles patrol around a home site (GPS within about 300 m). The scenario E GPS fault and the scenario G reverse-order trail are placed on the vehicle's own beat.
- **Badge with face.** A background face match at a gate comes with that person's badge use at the reader the camera watches. Badge and face of the same person happen together, as in reality.
- **No impossible duplicates.** A re-sent (altered) duplicate can overtake the original in transit, but never arrives before the event occurred.

## Ground truth

The ground-truth file is **for evaluation only**. Production records contain no scenario ids, roles or answers; `check_ground_truth` searches every line for leaks. Later phases must infer relationships from observable evidence.

```jsonc
{
  "purpose": "EVALUATION ONLY ...",
  "generator": {"version": "1.1.0", "seed": 42, "count": 50000, "profile": "mixed", "start": "...", "end": "...", "rates": {...}},
  "data_file": "data/generated/events_50000.jsonl",
  "expected_outcomes": {"total_submissions": 50000, "accepted": 47500, "invalid": 1500, "duplicate": 1000, "exact_duplicate": 476, "conflicting_duplicate": 524},
  "source_counts": {...}, "scenario_counts": {...}, "scenario_variant_counts": {...}, "anomaly_counts": {...},
  "scenarios": [{
    "scenario_id": "SCN-A-0001", "type": "A_multi_sensor_traffic_incident", "variant": "red_light",
    "title": "...", "description": "...", "expected_incident": true,      // true | false | "separate" | "uncertain"
    "related_event_ids": ["evt-c484cc8ba760", "..."], "unrelated_event_ids": ["evt-937525fd72f4"],
    "submission_indexes": [ ... ],                                         // 0-based file positions
    "expected_anomalies": [{"kind", "event_ids", "submission_indexes", "detail"}],
    "expected_conflicts": [...], "expected_uncertainty": [...],
    "expected_absences": [{"device_id", "source_type", "from", "to", "detail"}]
  }],
  "must_remain_separate": [{"scenario_id", "reason", "groups": [["evt-…"], ["evt-…"]]}],
  "submissions": [{"index": 0, "line": 1, "event_id": "evt-…", "expected_status": "accepted|invalid|duplicate",
                   "duplicate_kind": "exact|conflicting", "duplicate_of_index": 17, "invalid_reasons": ["..."], "scenario_ids": ["..."]}]
}
```

Records without a usable `event_id` (malformed lines, missing ids), and duplicates that share an id, are referenced by **submission index** and line number.

### Integrity checks ([`datagen/integrity.py`](../backend/datagen/integrity.py))

- One `submissions` entry per line, in order. Entries with an expected accepted or duplicate status carry the same `event_id` as the record.
- Each `event_id` is accepted exactly once. `duplicate_of_index` points at the accepted original. An `exact` duplicate is identical to its original and a `conflicting` one is not.
- `expected_outcomes` equals the tallies.
- Every referenced `event_id` is an accepted submission and every index is in range. Related and unrelated sets do not overlap, nor do separate groups.
- No observation from a failed device falls inside its expected absence.
- No label keys or scenario markers appear in any record.

The integrity checks run in the CLI, in `tests/test_generator.py`, and in `./evaluate.sh`. `tests/test_dataset_ingest.py` and the benchmark also ingest generated data through the API and compare **every** submission's actual outcome with the ground truth.
