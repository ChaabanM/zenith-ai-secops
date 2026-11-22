# Data quality and anomaly detection (Phase 3)

Code: [`backend/app/services/quality/`](../backend/app/services/quality/). The engine is deterministic and rule-based, with no AI and no external services. Every finding is a `QualityIssue` that carries:

- a plain-language **description** (what is wrong);
- a **detection reason**, built from the actual values (why it was flagged);
- **uncertainty** and **missing evidence** where they apply;
- links to the supporting **events and raw submissions**.

A quality issue describes the reliability of data. **It does not by itself mean a security incident occurred.**

## Workflow

1. `POST /ingest` stores the data (Phase 2) and calls `QualityScheduler.request()`. The response includes `"quality_analysis": "scheduled"`.
2. A background thread runs the full analysis. Bursts of ingest requests coalesce into a single follow-up run, so ingestion latency is unaffected. At 50K events, issues are queryable a few seconds after the ingest returns (see [benchmarks](benchmarks.md#phase-3-data-quality-analysis)).
3. `POST /quality-issues/analyze` runs the analysis synchronously, for scripts and for the **Run analysis** button.
4. `GET /quality-issues/summary` reports the latest run and `analysis_pending`. The Data Quality page refreshes itself while an analysis is pending.

Set `ZENITH_QUALITY_AUTO_ANALYZE=false` to disable the background run (the tests do this and run the analysis explicitly).

### Why a full, idempotent re-analysis

Each run analyses the whole database with streamed, index-backed queries. The rules are cross-event (ordering per device, baselines for spikes, conflicts between sensors), so new data can change earlier conclusions. For example, a recovery report turns an "offline, no recovery" finding into a recovered outage, and a larger baseline removes a spike. A full run keeps the stored issues exactly consistent with the evidence. Results are synced by **fingerprint**:

| Situation on re-run | Effect |
|---|---|
| Same finding, same content | Unchanged. Only `last_detected_at` / `last_run_id` are refreshed. |
| Same finding, content changed (for example, the outage recovered) | Updated in place. `first_detected_at` is kept. |
| New finding | Inserted. |
| Finding no longer supported by the evidence | Removed. |

Re-running on unchanged data never duplicates issues. The benchmark verifies `inserted = updated = removed = 0` on the second run at 50K. Analyses are serialized by a process-wide lock.

**Trade-off:** each run costs about 5 s at 50K events, independent of batch size. An incremental, window-scoped analysis would be the next optimisation for much larger volumes.

## Storage

| Table | Contents |
|---|---|
| `quality_issues` | `fingerprint` (unique, deterministic), `issue_type`, `subtype`, `rule_id`, `rule_version`, `severity` + `severity_rank`, `title`, `description`, `detection_reason`, `confidence` (heuristic rule confidence score; see below), `uncertainty`, `missing_evidence`, `details` (JSON with structured facts), `occurred_from`/`occurred_to` (event-time span of the evidence), `time_basis`, `event_count`, `submission_count`, `status`, `first_detected_at`, `last_detected_at`, `last_run_id`, `content_sha256` |
| `quality_issue_evidence` | One row per evidence link: `issue_id`, `position`, `role` (for example `badge_use`, `face_match`, `original_submission`), `event_pk` → `events`, `submission_id` → `raw_submissions`, `external_event_id`. Indexed by issue, event, submission and external id. |
| `quality_issue_facets` | `(issue_id, kind ∈ device/zone/source, value)`, indexed by `(kind, value, issue_id)` for filtering |
| `quality_runs` | Trigger, status, duration, counts analysed, inserted/updated/unchanged/removed, per-rule counts and timings, process peak memory, error |

Every issue is traceable to the original evidence. Events reference their raw submission, and rejected records are referenced by submission id only. `GET /events/{id}` also lists the quality issues that involve the event.

**Time basis.** An issue's time span is the event time of its evidence. Invalid records have no trustworthy event time, so they use the ingestion time (`time_basis = "ingested_at"`).

## Rules

| Rule | Issue type (subtypes) | Requirement | Logic | Severity |
|---|---|---|---|---|
| DUP-001 | `duplicate_submission` (`exact`, `conflicting`) | A | One issue per later submission whose `event_id` was already stored (from the Phase 2 raw layer). Evidence: the stored event, the original submission and the repeated submission. Conflicting resubmissions list every differing field with the stored and resubmitted values (payload compared key by key). Similar observations with different ids are never duplicates. | exact low, conflicting medium |
| VAL-001 | `invalid_record` (`malformed_json`, `not_an_object`, `oversized_record`, `missing_mandatory_field`, `invalid_timestamp`, `invalid_coordinates`, `invalid_confidence`, `incorrect_type`, `unknown_source_type`, `schema_violation`) | B | One issue per invalid submission, reusing the Phase 2 validation errors. Rejected request bodies are included. Missing mandatory fields are named in `missing_evidence`. Device, zone and source facets come from the submitted (unvalidated) content. | medium |
| DLY-001 | `delayed_event` | C | `received_at − occurred_at` above the per-source threshold. The actual delay is stored. Ordinary latency is not reported. | low; medium if ≥ 1 h |
| ORD-001 | `out_of_order_arrival` | D | **Basis: ingestion sequence (raw submission id) within one device.** Different devices and sources interleave legitimately and are never compared. An event is late when it was ingested after an event from the same device that occurred more than the per-source jitter tolerance later. Only events with plausible timestamps can be the reference, so a device whose clock runs ahead (TSC-001) does not make every later event look late. All events behind the same reference form one finding (one late burst). | low |
| TSC-001 | `timestamp_conflict` (`received_before_occurred`) | E | `received_at` earlier than `occurred_at` by more than the clock-skew tolerance. | medium |
| TSC-002 | `timestamp_conflict` (`linked_records_disagree`), `conflicting_sensor_results` (`traffic_measurement`) | E, G | A violation record **explicitly references** a radar track (`radar_device_id` + `radar_track_id`). The closest radar event with that track within ±1 h (track ids are recycled) must agree on time (≤ 30 s) and speed (≤ 10 km/h). Independent events are never required to share timestamps. | medium |
| LOC-001 | `location_conflict` (`impossible_travel`, `position_jump`) | F | Observations linked by an identical, fully read `plate_number` (ANPR, enforcement, phone and seatbelt cameras, and fleet GPS naming its own plate). Each observation is compared with the latest observation of that plate from every device, including the same GPS unit's previous fix. A conflict needs a distance above 1 km (plus 0.5 km when GPS is involved) **and** an implied speed above 200 km/h. Ordinary movement and GPS jitter stay below these limits. | medium |
| SNS-001 | `conflicting_sensor_results` (`vehicle_identification`) | G | The same fully read plate from different devices within 1 h reports a different make or colour. Partial or unreadable plates are never identifiers, so different vehicles at one gate are never compared. | medium |
| SNS-002 | `conflicting_sensor_results` (`identity`); `uncorrelatable_event` (`contradictory_identifiers`) | G, L | A confident face match (score ≥ 0.8) and a granted badge in the same zone within 3 s, where they are the **only** badge use and face event in that window, name different people. A weak or uncertain face candidate is never called a conflict. With exactly one badge use within 60 s, it is reported as unresolved identity evidence. | high / low |
| SNS-003 | `conflicting_sensor_results` (`sensor_outcome`) | G | A perimeter alarm (fence cut, climb or beam break, or high severity) followed within 120 s by a camera in the same zone reporting the verification result `no_activity`. | high |
| REP-001 | `repeated_device_events` | H | The same non-routine event type from one device ≥ 10 times within 15 min. Routine telemetry types (heartbeat, position_update, plate_read, access_granted, …) and informational events are excluded. | low; medium at ≥ 2 × threshold |
| SPK-001 | `event_spike` (`device`, `zone_event_type`, `zone`, `event_type`) | I | Sliding **event-time** windows (5 and 30 min) per key. The baseline is the key's average rate over the analysed period × a 1.5 daytime factor. The threshold is the Poisson upper-tail count with false-alarm probability `alpha / windows tested` (Bonferroni-style correction, alpha 0.01), and at least 8 events and 3 × expected. Dimensions run from most to least specific; an episode mostly covered by a more specific one is not repeated. | medium; high at ≥ max(20, 2 × threshold) |
| HLT-001 | `device_offline`, `device_degraded`, `sensor_failure`, `device_recovered`, `device_health_warning` | J | From `device_health` observations only. `device_id` is the **affected** device; `payload.monitor` is the **reporting** monitor. Offline issues link the recovery report and the downtime. Without a recovery report the issue is critical and the device is treated as still offline. Every issue carries the device's latest reported state. Heartbeats above the temperature or packet-loss limits are warnings. **Silence is never treated as an outage**, because no heartbeat contract is documented for the sources. | offline high (critical if unrecovered), failure high, degraded medium, warning low, recovered informational |
| LCF-001 | `low_confidence_detection` | K | Confidence below the per-source threshold, or the sensor itself reports an uncertain result (ANPR `read_status` partial or unreadable, face `match_status` uncertain). The text states that low confidence means **uncertain evidence, not a confirmed false detection**. | low; medium if the event is a high or critical alert |
| UNC-001 | `uncorrelatable_event` (`missing_plate`, `missing_track`, `uncertain_identity`) | L | A person or vehicle observation without a usable identifier: an ANPR read with no or partial plate, a CCTV person/vehicle detection without a track id, or a face result with only a weak candidate. | low |
| UNC-002 | `uncorrelatable_event` (`missing_location`) | L | No zone and no coordinates. | low |
| UNC-003 | `uncorrelatable_event` (`uncorroborated_report`) | L | A manual incident report (not a shift note) that references no events, with no medium-or-higher sensor event in the same zone within ±15 min. | low |

### Conservative Phase 3 criteria for "cannot be reliably correlated"

Phase 3 does not correlate events (that is Phase 4). It flags only observations whose **own content** makes linkage unreliable: a missing identifier, a missing location, an uncorroborated manual report, or contradictory identity evidence too weak to resolve. Ordinary standalone events (plate reads with a full plate, telemetry, shift notes) are never flagged.

### Not used

- The generator's ground truth. Production code never reads it; a test checks that `app/` never mentions it.
- Silence as evidence of an outage.
- LLM-generated text. All explanations are deterministic templates filled with the actual values.

## Thresholds and settings

Environment variables are `ZENITH_QUALITY_<NAME>`. Dictionaries and lists are given as JSON. Defaults are in [`configuration.py`](../backend/app/services/quality/configuration.py).

| Setting | Default |
|---|---|
| `AUTO_ANALYZE` | `true` |
| `DELAY_THRESHOLD_SECONDS` | `{"default": 300, "operator_report": 1800, "weather_environmental": 600, "device_health": 600}` |
| `DELAY_MEDIUM_SECONDS` | 3600 |
| `OUT_OF_ORDER_TOLERANCE_SECONDS` | `{"default": 20, "weather_environmental": 60, "operator_report": 300}` (manual reports are typed with variable lag) |
| `CLOCK_SKEW_TOLERANCE_SECONDS` | 60 |
| `LINKED_TIMESTAMP_TOLERANCE_SECONDS` / `LINKED_TRACK_SEARCH_SECONDS` / `LINKED_SPEED_TOLERANCE_KPH` | 30 / 3600 / 10 |
| `LOCATION_MIN_DISTANCE_KM` / `GPS_ERROR_ALLOWANCE_KM` / `MAX_PLAUSIBLE_SPEED_KPH` | 1.0 / 0.5 / 200 |
| `VEHICLE_DETAIL_WINDOW_SECONDS` | 3600 |
| `IDENTITY_PAIR_WINDOW_SECONDS` / `IDENTITY_MIN_FACE_SCORE` / `UNRESOLVED_IDENTITY_WINDOW_SECONDS` | 3 / 0.8 / 60 |
| `PERIMETER_VERIFICATION_WINDOW_SECONDS` | 120 |
| `REPEAT_WINDOW_SECONDS` / `REPEAT_MIN_COUNT` / `TELEMETRY_EVENT_TYPES` | 900 / 10 / heartbeat, position_update, weather_reading, plate_read, access_granted, auth_success, face_match, vehicle_detected, crowd_count, shift_note |
| `SPIKE_WINDOWS_SECONDS` / `SPIKE_MIN_COUNT` / `SPIKE_RATIO` / `SPIKE_ALPHA` / `SPIKE_DIURNAL_FACTOR` / `SPIKE_MAX_EVIDENCE` | [300, 1800] / 8 / 3 / 0.01 / 1.5 / 300 |
| `HEALTH_TEMPERATURE_WARNING_C` / `HEALTH_PACKET_LOSS_WARNING_PCT` | 75 / 5 |
| `LOW_CONFIDENCE_THRESHOLDS` | `{"default": 0.5, "face_recognition": 0.6, "anpr": 0.6, "cctv": 0.5, "perimeter_intrusion": 0.4}` (perimeter sensors routinely report 0.4–0.7 for wind and wildlife) |
| `REPORT_CORROBORATION_WINDOW_SECONDS` | 900 |

## Confidence and uncertainty

- `confidence` on an issue is a **heuristic rule confidence score**: a deterministic value set by the rule, not a calibrated probability that the finding is correct. The UI shows it as "Rule confidence score: 0.90 (heuristic)" (never as a percentage). It is 1.0 for direct facts such as a duplicate id or a missing field. It is lower where the rule infers something, such as pairing a badge use with a face by time and place (0.9 or below), or an uncorroborated report (0.7). It is never the sensor's detection confidence; that value appears in the evidence and the detection reason.
- `uncertainty` states what the data cannot tell. Examples: which of two conflicting records is right, the true occurrence time of a skewed event, and whether a spike baseline is reliable with little history.
- `missing_evidence` names what would resolve the issue, such as a recovery report, a full plate, or a corroborating observation.

## Ground-truth evaluation (50K mixed, seed 42)

`backend/datagen/quality_eval.py` compares the stored issues with the generator's labels. It is evaluation tooling only.

- **Precision** = issues that touch a labelled finding of the mapped kinds / all issues of the type.
- **Recall** = labelled findings touched by at least one issue / all labelled findings of those kinds.
- **FP-explained**: issues that touch a different labelled anomaly. These are often real problems labelled under another name.
- **FP-background**: issues that touch only unlabelled ordinary events.

| Issue type | Issues | TP | FP (other label) | FP (background) | Precision | Labelled findings | Found | FN | Recall |
|---|---|---|---|---|---|---|---|---|---|
| `duplicate_submission:exact` | 476 | 476 | 0 | 0 | 1.000 | 476 | 476 | 0 | 1.000 |
| `duplicate_submission:conflicting` | 524 | 524 | 0 | 0 | 1.000 | 524 | 524 | 0 | 1.000 |
| `invalid_record` | 1,500 | 1,500 | 0 | 0 | 1.000 | 1,500 | 1,500 | 0 | 1.000 |
| `delayed_event` | 1,315 | 1,265 | 50 | 0 | 0.962 | 118 | 118 | 0 | 1.000 |
| `out_of_order_arrival` | 157 | 51 | 106 | 0 | 0.325 | 50 | 50 | 0 | 1.000 |
| `timestamp_conflict` | 200 | 200 | 0 | 0 | 1.000 | 100 | 100 | 0 | 1.000 |
| `location_conflict` | 61 | 61 | 0 | 0 | 1.000 | 50 | 50 | 0 | 1.000 |
| `conflicting_sensor_results` | 149 | 149 | 0 | 0 | 1.000 | 150 | 149 | 1 | 0.993 |
| `repeated_device_events` | 55 | 36 | 18 | 1 | 0.654 | 38 | 38 | 0 | 1.000 |
| `event_spike` | 62 | 44 | 18 | 0 | 0.710 | 50 | 43 | 7 | 0.860 |
| `device_offline` | 50 | 50 | 0 | 0 | 1.000 | 50 | 50 | 0 | 1.000 |
| `device_degraded` | 50 | 50 | 0 | 0 | 1.000 | 50 | 50 | 0 | 1.000 |
| `sensor_failure` | 50 | 50 | 0 | 0 | 1.000 | 50 | 50 | 0 | 1.000 |
| `device_recovered` | 50 | 50 | 0 | 0 | 1.000 | 50 | 50 | 0 | 1.000 |
| `low_confidence_detection` | 1,262 | 1,001 | 261 | 0 | 0.793 | 1,472 | 1,472 | 0 | 1.000 |
| `uncorrelatable_event` | 1,038 | 132 | 906 | 0 | 0.127 | 100 | 93 | 7 | 0.930 |

On the clean 50K profile the figures are identical except that there are no duplicate or invalid issues. FP (background) there is 2 for repeated alerts and 2 for spikes; see below.

Notes on the numbers:

- **Delayed (FP-explained 50):** the scenario E enforcement-camera records whose clock is 5–12 min behind are, by their own timestamps, delivered 5–12 min late. That is correctly flagged, but labelled only as a timestamp conflict.
- **Out-of-order (FP-explained):** store-and-forward uploads deliver a device's buffered events after its newer live events. That genuinely is out-of-order arrival, but the labels call it "delayed". No ordinary background event is flagged.
- **Low confidence (FP-explained):** degraded-device output (scenario D) and weak scenario J/L detections really do have low confidence; they are labelled under their scenario instead.
- **Cannot be reliably linked:** precision against the scenario L labels is low by construction. Unreadable or partial plates and uncertain faces from scenario I also lack identifiers and are correctly flagged (all of them FP-explained). Recall misses: several scenario L "vague report" cases had an unrelated medium-severity alert in the same zone within 15 min, so the conservative rule treats them as possibly corroborated.
- **Spikes:** spikes also fire on bursts labelled as other anomalies (store-and-forward bursts, denial bursts, reverse GPS trails), so label precision understates usefulness. The misses are small speeding clusters (5–7 vehicles in 20 min) that do not reach statistical significance against the site's baseline.
- **Identity:** a scenario E badge/face pair is skipped when another badge use falls in the same 3 s window. The pairing is ambiguous, so the rule does not guess.
- **Repeated alerts / spikes on background (1–4 findings):** busy intersection radars occasionally reach 10 `speed_violation` alerts in 15 min from ordinary traffic plus a scenario A pass. On the clean profile, two daytime intersection zones show 23–24 events in 5 min (normal about 6.5) because a scenario A multi-sensor capture lands on a busy period. These are plausible operational signals, not data errors.

## Known false-positive and false-negative limitations

- Spike baselines are averages over the loaded data. With little history (for example the 500-record demo) they are crude, and short bursts can be reported. The issue's uncertainty says so.
- Identity pairing relies on time and place, so a second person passing unseen would produce a false conflict. Ambiguous windows are skipped, which trades recall for precision.
- Location conflicts need a fully read plate. Partial plates and vehicles without plates are never compared.
- Out-of-order detection is per device. Cross-device ordering anomalies are not inferred.
- Health state comes only from reported health events. A device that silently dies without a health monitor report is not detected.
- Low-confidence thresholds are per source, not calibrated per device or model version.

## Identity pairing wording (Phase 7B)

The identity rule (G, `conflicting_sensor_results` / `identity`) pairs a granted badge use and a face match only when they are the only two such events at the same zone within the pairing window. It never claims they belong to one person. Its detection reason reads:

> "These observations were provisionally paired for conflict analysis because they were the only badge and face-recognition events within the time window. It is not verified that they concern the same person."

The rule, its window, severity, score and evidence are unchanged. The incident engine uses the same language for the matching link ("provisionally paired, not verified to be the same person"). Onward vehicle movement is described as "the same fully read plate", noting that a cloned or misread plate would also explain it. `tests/test_wording.py` guards these phrasings.
