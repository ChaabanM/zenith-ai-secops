# Requirements traceability

## Final requirements matrix (Phase 7A audit, 2026-11-22; updated after Phase 7B and the Docker verification)

This matrix covers every mandatory requirement in Sections 1–11 of the *Detailed Requirements v3*. The per-phase tables below have the detail. They are historical: each records the state at the end of its phase, so their Docker rows say "pending" and their test counts are those of that phase.

**Current totals:** 421 backend and 77 frontend tests; Phase 7B headless-Chrome checks 26 of 26; Docker manually verified on macOS (see the row for § 8).

**Verification methods used in the audit:**

- `./evaluate.sh` in a fresh clone from GitHub;
- `./evaluate.sh --benchmark` (50K clean and mixed);
- the backend test suite on Python 3.11.11 and 3.12.10;
- an HTTP audit script with 52 checks on a clean install loaded with the demo data;
- an end-to-end evidence-chain script with 13 checks;
- headless-Chrome checks from Phases 4B–7B;
- a manual Docker verification on macOS with Docker Desktop (after Phase 7B).

**Result values:**

- **pass**: verified in this audit;
- **unverified**: not executed, with the reason given;
- **n/a**: not required.

### Mandatory

| § | Requirement | Implementation | Verification | Result | Limitations |
|---|---|---|---|---|---|
| 1 | Runs on a normal laptop, no GPU, no paid API key | FastAPI + SQLite + React; deterministic reasoning | all runs on an Apple M5 laptop, no network services; 50K peak server RSS 188 MB | pass | — |
| 1 | Explain tools used and how output was verified | README *AI tools used*, [reasoning.md](reasoning.md) | document review | pass | — |
| 2 | Help answer: what is happening, related events, reliability, immediate attention, what happened in a shift, next action | Dashboard, Incidents, Data Quality, Briefings | browser checks (Phases 4B–6); [dashboard.md](dashboard.md) | pass | — |
| 3 | 15 source categories, documented schema | `schemas/sources.py`, [event-schema.md](event-schema.md) | `test_ingest.py` covers every source and its aliases | pass | Payload contents are not validated per source |
| 4.1 | Duplicates, malformed/incomplete, missing mandatory fields, delayed/out-of-order, conflicting timestamps/locations/sensors, repeated device events, spikes, offline/unhealthy devices, low confidence, uncorrelatable; originals preserved; reason for each | ingestion validation + 17 quality rules, raw submissions kept | `test_quality_rules.py`; demo ground truth (every type at recall 1.000); 50K evaluation in [data-quality.md](data-quality.md); SHA-256 of all 520 stored submissions re-checked | pass | Low-confidence and uncorrelatable precision is 0.5 on the demo (expected findings outside the labelled scenarios) |
| 4.2 | Group related events into incidents with title/category, severity/confidence, status, timeline, sources, devices/locations, linkage explanation, conflicts/missing information, actions, original event IDs; no assumptions as facts | correlation engine, `GET /incidents/{id}`, investigation UI | 50K: precision 0.9937, recall 0.9915, 0 labelled false merges; every timeline reference resolved (audit) | pass | Ambiguity is resolved by not linking (151 false splits at 50K); scores are heuristic |
| 4.3 | Shift/daily briefing: priority, unresolved, new incidents, repeated events by device/location, device health, trends/spikes, data quality, actions, confidence, event references; for a manager | `GET /briefing`, Briefings page | `test_briefing.py` (32); benchmark checks 26 briefings against SQL (all references valid) | pass | Status is current (no history); "significant" is Poisson screening |
| 5 | Small sample dataset; generator for ≥ 50,000 events with all required data problems; no chronological-arrival assumption | `data/samples/`, `datagen/`, `scripts/generate_events.py` | demo reproducibility and ground-truth integrity (`evaluate.sh`); 50K runs | pass | — |
| 6 | AI where it adds value; reference event IDs; facts vs assumptions; uncertainty; missing information; never invent evidence; structured output; deterministic fallback | rules, heuristics, Poisson statistics, templates; optional validated local-model wording | `test_reasoning.py` (fabricating and failing providers fall back); `grounding.verify_references` on every briefing and dashboard | pass | No real language model was exercised (Ollama not installed); the default is deterministic |
| 7 | Web UI: load/submit events; incidents and timelines; filter by severity, source, location, device, time; quality issues; briefing; inspect source events behind any generated result; usable without raw JSON | React SPA (Dashboard, Events, Incidents, Data Quality, Briefings) | 77 frontend tests; headless Chrome (20 + 16 + 22 checks in Phases 4B–6, 26 in Phase 7B; no console errors) | pass | Mobile app (optional) not built; responsive web only |
| 8 | Project structure, documented schema, API/service layer, persistent storage | `backend/app/{api,services,models}`, SQLite (WAL) | review; [architecture.md](architecture.md) | pass | SQLite allows a single writer |
| 8 | Input validation, error handling, automated tests, offline/demo mode | validation + one error envelope; 421 + 77 tests; deterministic, offline by default | API audit: 52 checks including 25 invalid-input cases, all in the error envelope, no unhandled exceptions | pass | — |
| 8 | Docker support **or** simple reproducible startup | `docker-compose.yml`, Dockerfiles; README *Run locally* and *Run with Docker* | local startup: fresh clone → venv → `pip install` → `npm ci` → servers → `scripts/load_demo.py`, all succeeded. Docker (manual, macOS, Docker Desktop): both images built; `docker compose up -d` started both services; backend `healthy`; `/health` returned `ok`, `docker`, database connected; the app loaded at :5173 with real stored data; after `docker compose restart` the backend was healthy again and the data was still there | pass | Docker verified on macOS only. Other operating systems were not checked, and the test suite and benchmarks were not run in containers. |
| 8 | Health-check endpoint | `GET /health` (503 when the database is unavailable) | API audit; `test_health.py` | pass | — |
| 8 | Seed data + generator for large sets | as § 5 | as § 5 | pass | — |
| 8 | `POST /ingest`, `GET /events`, `GET /incidents`, `GET /incidents/{id}`, `GET /quality-issues`, `GET /briefing`, `GET /health` | `app/api/*` | API audit: success, invalid input, pagination, filters, 404s, reference integrity for each | pass | — |
| 9 | Submit valid/invalid/duplicate/delayed/conflicting/malformed events; ≥ 50k events; responsive, no crashes; IDs preserved; related grouped; unrelated separate; quality and uncertainty surfaced; evidence-backed briefing | all of the above | API audit; 50K: every record checked against ground truth, full traversal (2.0 s), page latency 3.7–4.6 ms, 0 labelled false merges | pass | — |
| 9 | Own test command | `./evaluate.sh [--benchmark]` | ran in a fresh clone and in the working copy | pass | Needs Python 3.11+ and Node 20.19+/22.12+ |
| 10 | Source, setup instructions, Dockerfile/reproducible startup, sample data + generator, tests, API docs, architecture, design decisions, AI tools, known limitations | README + `docs/` | review | pass | — |
| 10 | Demo video or written demo guide (≤ 5 min) | README *Demo walkthrough* (written) | review | pass (written guide) | No video recorded yet; README has a pending video-link field |
| 10 | Public repository | GitHub | anonymous `git clone https://github.com/ChaabanM/zenith-ai-secops-platform` succeeded | pass | — |
| 11 | All three workflows functional in one platform | Data Quality → Incident → Briefing | evidence-chain audit: submission → event → quality issue → incident → briefing and back, all references resolve | pass | — |

### Optional bonus objectives (Section 12): not implemented, by instruction

| Bonus | Status |
|---|---|
| A: voice / Jarvis assistant (`POST /assistant/query`, `POST /assistant/confirm`, `GET /assistant/history`) | not implemented |
| B: automated evaluator for the Section 9 checks | not implemented as a separate deliverable. `./evaluate.sh --benchmark` covers much of Section 9 but is not packaged as the bonus evaluator. |
| C: mobile app | not implemented (the web UI is responsive down to 390 px) |


## Phase 2: ingestion, persistence and dataset

Latest run, `./evaluate.sh --benchmark` on 2026-11-22: 332 backend tests passed, the frontend build passed, and both 50K tests (ingestion, quality analysis, correlation) passed.

| # | Requirement | Implementation | Verification | Result |
|---|---|---|---|---|
| 2 | 15 canonical sources + aliases | `app/schemas/sources.py`, [event-schema.md](event-schema.md) | `test_ingest.py::test_every_mandatory_source_is_accepted` (15), `test_source_aliases_resolve`, `test_alias_is_normalized_and_original_kept_in_evidence` | pass |
| 3 | Event schema, mandatory/optional, type and range validation | `app/schemas/events.py`, `services/ingestion/records.py` | `test_missing_mandatory_field_is_invalid`, `test_invalid_timestamps`, `test_invalid_coordinates`, `test_invalid_confidence`, `test_severity_validation_and_aliases`, `test_payload_must_be_an_object`, `test_bad_identifier_values_are_invalid` | pass |
| 3 | Invalid vs suspicious; never invent missing data | `records.validate_event` (warnings) | `test_missing_optional_fields_are_preserved_as_unknown`, `test_delayed_event_is_valid`, `test_received_before_occurred_is_kept_as_suspicious`, `test_low_confidence_event_is_valid` | pass |
| 3 | UTC-aware datetimes | `parse_utc_timestamp`, `models/types.UTCDateTime` | `test_timezone_offsets_are_converted_to_utc`, `test_filter_by_time_range` | pass |
| 4 | Raw submissions layer (all outcomes, evidence, size limits) | `models/ingestion.RawSubmission`, `service.record_rejected_request` | `test_mixed_batch_is_partially_accepted`, `test_malformed_json_body_is_400_and_preserved`, `test_oversized_record_is_invalid_with_truncated_evidence`, `test_unknown_fields_are_ignored_but_kept_in_evidence` | pass |
| 4 | Normalized events, indexes, uniqueness contract, no overwrite | `models/ingestion.Event`, `service.BatchIngestor` | `test_conflicting_duplicate_never_overwrites_normalized_event`, `test_invalid_submission_does_not_claim_event_id`, `test_same_device_time_location_with_different_ids_are_both_kept`, `test_batches_are_processed_in_bounded_chunks` | pass |
| 4 | Transactional consistency | per-chunk transaction in `BatchIngestor.flush` | benchmark per-record verification (raw ↔ event links for 50,000 records) | pass |
| 5 | POST /ingest: single, array, wrapper, partial acceptance, status codes | `api/ingest.py` | `test_valid_single_event`, `test_json_array_batch`, `test_wrapper_object_batch`, `test_invalid_single_event_returns_422_with_record_details`, `test_bad_request_bodies`, `test_multipart_upload_is_rejected`, `test_oversized_json_body_is_413` | pass |
| 5 | JSONL bounded-batch import; malformed lines do not stop later ones | `services/ingestion/jsonl.py`, `_ingest_jsonl` | `test_jsonl_with_malformed_lines_continues`, `test_jsonl_crlf_and_missing_final_newline`, `test_jsonl_format_query_parameter_and_non_utf8`, `test_jsonl_streams_across_many_chunks` | pass |
| 5 | Duplicate reporting, record-level details, raw-submission inspection endpoint | `/submissions`, `/batches` | `test_exact_duplicate_in_same_request`, `test_batch_record_reports_are_bounded`, `test_submissions_and_batches` | pass |
| 6 | GET /events pagination, filters, stable sort | `api/events.py`, `services/ingestion/queries.py` | `test_events_api.py` (21 tests), benchmark full traversal | pass |
| 6 | GET /events/{id}: evidence, original id, provenance | `api/events.get_event` | `test_event_detail_exposes_evidence_and_provenance`, `test_original_event_id_is_preserved_everywhere` | pass |
| 7 | Deterministic generator, CLI, rates, window, JSON/JSONL | `backend/datagen/generator.py`, `scripts/generate_events.py` | `test_same_seed_is_byte_identical`, `test_exact_submission_count` (16), `test_rates_are_configurable`, `test_cli_writes_jsonl_and_rejects_bad_arguments` | pass |
| 7 | Realistic fictional world; plausible identifiers | `backend/datagen/world.py` | review + `test_all_fifteen_sources_are_generated_and_valid` | pass |
| 8 | Scenarios A–L with all sub-variants | `generator.scn_*` | `test_all_scenario_types_present`, `test_device_failure_absence_has_no_fabricated_events`, `test_unrelated_events_must_remain_separate` | pass |
| 9 | Multiple shifts and days | `diurnal_time`, 3-day window | `test_events_span_multiple_shifts_and_days` | pass |
| 10 | Ground truth, integrity, no leakage | `GeneratedDataset.ground_truth`, `datagen/integrity.py` | `test_ground_truth_integrity`, `test_integrity_checker_detects_corruption`, `test_no_label_leakage`, `test_dataset_ingest.py` (outcome per record) | pass |
| 11 | Committed demo dataset; generated 50K ignored; reproducible | `data/samples/`, `.gitignore` | `test_committed_demo_dataset_is_reproducible`, `test_demo_dataset_covers_every_source_and_scenario`, `evaluate.sh` | pass |
| 12 | Test A: clean 50K | `scripts/benchmark_ingest.py` | [benchmarks.md](benchmarks.md) | pass: 50,000/50,000 |
| 12 | Test B: mixed 50K, exact outcome counts | `scripts/benchmark_ingest.py` | [benchmarks.md](benchmarks.md) | pass: 47,500 / 1,500 / 1,000 (476/524, generator 1.1.0) |
| 13 | Events page: listing, server pagination, filters, detail, evidence, JSON/JSONL upload, summary, errors, loading/empty states | `frontend/src/pages/events/` | `npm run build`; manual run against the demo data with headless Chrome screenshots (list, filters, empty state, detail with duplicates) | pass |
| 14 | Phase 1 regression | existing `test_health.py`, `test_database.py` | pytest | pass |
| 14 | `./evaluate.sh` | `evaluate.sh` | run | pass |

## Phase 3: data quality and anomaly detection (requirements Section 4.1)

Tests are in `backend/tests/test_quality_rules.py` (rules), `test_quality_api.py` (API and workflow) and `test_quality_ground_truth.py` (precision/recall floors). The 50K measurements are in [benchmarks.md](benchmarks.md#phase-3-data-quality-analysis).

| # | Requirement | Implementation | Verification | Result |
|---|---|---|---|---|
| 2A | Exact duplicates; repeated IDs with conflicting payloads; both submissions referenced | `rules/records.py` DUP-001 | `test_exact_and_conflicting_duplicates`, `test_similar_observations_with_different_ids_are_not_duplicates`; ground truth precision and recall 1.0 | pass |
| 2B | Malformed JSON/JSONL, missing fields, invalid timestamps/types/coordinates/confidence, other schema violations | `rules/records.py` VAL-001 | `test_malformed_and_incomplete_records`, `test_rejected_request_body_is_an_issue`; ground truth 1500/1500 | pass |
| 2C | Delays above a configurable threshold; actual delay kept; not every delay | `rules/timing.py` DLY-001 | `test_delay_threshold_and_per_source_limits`, `test_interleaved_devices_jitter_and_late_but_valid_events_are_not_out_of_order` | pass |
| 2D | Genuine out-of-order arrival; ordering basis explained; multi-device | `rules/timing.py` ORD-001 | `test_out_of_order_arrival_same_device`, `test_interleaved_devices_…`, `test_future_clock_does_not_make_every_later_event_out_of_order`; 0 background false positives at 50K | pass |
| 2E | Received before occurred (skew tolerance); contradictory linked timestamps | `rules/timing.py` TSC-001, TSC-002 | `test_received_before_occurred_respects_skew_tolerance`, `test_linked_radar_and_violation_disagree_on_time`, `test_unrelated_events_with_similar_timestamps_are_not_conflicts` | pass |
| 2F | Location conflicts with entity link and physical movement limits; no false conflicts from movement or GPS error | `rules/entities.py` LOC-001 | `test_impossible_travel_is_a_location_conflict`, `test_normal_vehicle_movement_and_gps_jitter_are_not_conflicts`; ground truth precision and recall 1.0 | pass |
| 2G | Conflicting vehicle identification, identity, traffic measurement, sensor outcome; only for the same entity; uncertainty stated | `rules/entities.py` SNS-001, `rules/identity.py` SNS-002/003, TSC-002 | `test_same_plate_different_vehicle_details`, `test_different_vehicles_at_the_same_gate_are_not_conflicts`, `test_badge_and_face_name_different_people`, `test_identity_negatives`, `test_perimeter_alarm_contradicted_by_camera`, `test_linked_speed_mismatch_is_a_traffic_measurement_conflict` | pass |
| 2H | Repeated abnormal alerts per device and window; telemetry excluded | `rules/volume.py` REP-001 | `test_repeated_alerts_vs_routine_telemetry` | pass |
| 2I | Spikes by device, zone, event type; event time; baselines | `rules/volume.py` SPK-001 | `test_volume_spike_against_baseline`, `test_steady_traffic_is_not_a_spike`, `test_spikes_rarely_fire_on_unlabelled_background` | pass |
| 2J | Offline, degraded, sensor failure, recovery, ongoing state; reporting vs affected device; no inference from silence; health/alias regression | `rules/health.py` HLT-001 | `test_device_lifecycle_degraded_failed_offline_recovered`, `test_offline_without_recovery_is_critical`, `test_health_report_about_aliased_camera_stays_device_health`, `test_silence_is_not_an_outage` | pass |
| 2K | Low confidence with per-source thresholds; "uncertain, not false" | `rules/evidence.py` LCF-001 | `test_low_confidence_thresholds_per_source`; ground truth recall 1.0 | pass |
| 2L | Insufficient linkage evidence, conservative criteria, no incidents | `rules/evidence.py` UNC-001/002/003, `rules/identity.py` (contradictory identifiers) | `test_uncorrelatable_criteria`, `test_weak_face_candidate_is_unresolved_not_a_conflict`, `test_ordinary_activity_produces_no_issues` | pass |
| 3 | Persistent QualityIssue model with explanation, evidence links, rule id/version, timestamps, facets | `models/quality.py`, `repository.py` | `test_detail_and_not_found`, benchmark evidence-integrity checks (0 broken references) | pass |
| 4 | Modular rule engine; streamed queries; idempotent re-runs; configurable thresholds; integrated with ingestion | `services/quality/` (`engine.py`, `rules/`, `repository.py`, `configuration.py`, `schemas.py`) | `test_reanalysis_is_idempotent`, `test_new_data_updates_and_removes_findings`, `test_ingest_triggers_background_analysis`; 50K re-run inserted/updated/removed = 0 | pass |
| 5 | `GET /quality-issues` with pagination, filters, stable sort, totals; detail endpoint | `api/quality.py` | `test_list_pagination_and_stable_sort`, `test_filters`, `test_invalid_filters`, `test_summary_and_event_cross_reference` | pass |
| 6 | Data Quality dashboard: totals, by type and severity, filters, table, drawer, explanation, clickable evidence, raw inspection, empty/loading states | `frontend/src/pages/quality/` | `npm run build`; headless Chrome screenshots of the dashboard, identity-conflict drawer, conflicting-duplicate drawer and the event → issue cross-link with the demo data | pass |
| 7 | Ground-truth comparison: TP/FP/FN, precision/recall; ground truth never used by production code | `backend/datagen/quality_eval.py`, `scripts/evaluate_quality.py` | [data-quality.md](data-quality.md#ground-truth-evaluation-50k-mixed-seed-42), `test_production_code_never_reads_ground_truth` | pass |
| 8 | 50K performance: time, memory, issues by type, storage, latency, ingestion impact, idempotency, evidence integrity | `scripts/benchmark_ingest.py` | [benchmarks.md](benchmarks.md#phase-3-data-quality-analysis) | pass |
| 9 | Documentation and `./evaluate.sh` | README, docs/*, `evaluate.sh` | `./evaluate.sh --benchmark` | pass |
| 10 | Docker | unchanged Dockerfiles | not executed (Docker not installed) | pending |

## Phase 4A: incident correlation (requirements Section 4.2)

Tests are in `backend/tests/test_correlation.py` (engine behaviour), `test_incidents_api.py` (API) and `test_correlation_ground_truth.py` (related vs unrelated groups against ground truth). The 50K measurements are in [benchmarks.md](benchmarks.md#phase-4a-incident-correlation).

| # | Requirement | Implementation | Verification | Result |
|---|---|---|---|---|
| 2 | Incident + evidence model; operator status separate from computed fields; no copied events | `models/incidents.py` | `test_detail_has_all_section_4_2_fields`, `test_status_update_and_validation`, benchmark integrity (0 broken references, 0 duplicate links) | pass |
| 3 | Modular engine; strong / contextual / negative evidence; no blind connected components; anti transitive merging | `services/correlation/*` | `test_shared_context_does_not_chain_two_incidents`, `test_two_alarms_sharing_a_camera_event_are_not_merged`, `test_impossible_travel_never_merges_and_is_surfaced`, `test_different_vehicles_at_the_same_site_stay_separate` | pass |
| 4 | Source vs linkage vs incident confidence; low-confidence evidence not promoted | `scoring.py`, `clustering._propose` | `test_low_confidence_detection_does_not_become_context`, `test_partial_plate_is_never_an_identifier`, `test_conflicts_lower_confidence` | pass |
| 5 | Categories, deterministic severity; routine not incidents; no inflation by duplicates | `clustering.py`, `explanations.py` | `test_routine_activity_creates_no_incidents`, `test_single_uncorroborated_violation_is_not_an_incident`, `test_duplicates_are_not_counted_as_corroboration`, `test_device_failure_episode`, `test_access_attempts_door_and_referenced_report` | pass |
| 6 | Quality integration as annotation, not exclusion | `explanations.build_incident`, `repository.quality_issues_for` | `test_quality_findings_annotate_but_do_not_drive_incidents`, ground-truth recall | pass |
| 7 | Scenarios A–L | builders | `test_correlation_ground_truth.py`; [correlation.md](correlation.md#ground-truth-evaluation) | pass (see the measured splits) |
| 8 | Explanations: observations / inferences / conflicts / missing / uncertainty / actions; timeline | `explanations.py`, `api/incidents.py` | `test_multi_sensor_traffic_pass_is_one_incident`, `test_detail_has_all_section_4_2_fields` | pass |
| 9 | Late arrivals, out-of-order, re-runs, identity stability, status preservation, merge/split policy, transactions, no concurrent runs | `repository.sync_incidents`, `engine` lock | `test_out_of_order_and_late_evidence_preserve_identity_and_status`, `test_merge_policy_keeps_history`, `test_reprocessing_is_idempotent_and_source_events_are_untouched` | pass |
| 10 | `GET /incidents` (pagination, severity, status, category, zone, device, time, sort) and `GET /incidents/{id}` | `api/incidents.py` | `test_list_pagination_and_sort`, `test_filters`, `test_invalid_filters` | pass |
| 11 | Bounded candidate selection, no pairwise scans, laptop memory | `candidate_selection.py` | 50K benchmark | pass |
| 12 | Ground truth never used by production code | n/a | `test_production_code_never_reads_ground_truth` (covers `app/services/correlation`) | pass |
| 13 | Source events never modified | n/a | `test_reprocessing_is_idempotent_and_source_events_are_untouched`, benchmark events fingerprint before/after | pass |

## Phase 4B: incident investigation dashboard (requirements Sections 4.2 and 7)

Frontend tests are in `frontend/src/pages/incidents/__tests__/` and `frontend/src/pages/events/__tests__/`. Backend tests for the API additions are in `backend/tests/test_incidents_api.py`. Browser checks were run in headless Chrome against a fresh database loaded with the demo dataset through the real pipeline. Details are in [investigation-ui.md](investigation-ui.md#verification).

| # | Requirement | Implementation | Verification | Result |
|---|---|---|---|---|
| 3 | Overview: summary cards (total, open, critical, high, investigating, resolved) and table with every required column; prioritization without changing severity | `IncidentsPage.tsx`; backend `sort=priority`, `unresolved_by_severity` | "shows real summary counts…", "renders table rows…", `test_priority_sort_puts_unresolved_first_then_severity`, `test_summary_unresolved_counts_follow_status`; browser check 2 | pass |
| 4 | Server-side filters (severity, source, zone, device, time, category, status), pagination, stable sort, clear, total, filters kept after returning | URL query, `GET /incidents` | "sends the * filter…", "sends device and time filters…", "defaults to the priority sort and server-side pagination", "restores filters from the URL…", "opens the investigation with the list query remembered"; browser checks 3 and 4; API walk of 718 unique ids at 50K | pass |
| 5 | Investigation view with every Section 4.2 field, evidence summary, relationship explanation, recommendations labelled as advisory | `IncidentDetailPage.tsx` | "renders every Section 4.2 field…", "lists recommended actions labelled as advisory…"; browser check 10 | pass |
| 6 | Chronological timeline (stable tie-break; not arrival order) with every required field; strong, contextual, conflicting and uncertain evidence distinguished; low confidence never shown as verified | `IncidentTimeline.tsx`, `labels.evidenceClass`; backend `low_source_confidence`, `link_description` | "orders the timeline by occurred_at…", "classifies evidence…", `test_timeline_flags_low_source_confidence_and_describes_links`; browser check 5 | pass |
| 7 | Traceability: incident → event → raw submission; incident → quality issue → supporting event; event → incident | reused `EventDetailPanel` (plus related incidents) and `QualityIssueDetailPanel` | "opens the source event with its original raw submission…", "opens a related quality issue…", "links an event back to the incidents that use it", `test_event_filter_links_event_back_to_its_incident`; browser checks 6–8 (raw record identical to `GET /submissions/{id}`) | pass |
| 8 | Facts vs inferences vs conflicts (side by side, no side chosen) vs missing vs uncertainty | `IncidentDetailPage.tsx`, `ConflictComparison.tsx` | "shows facts, inferences, conflicts side by side…", "shows truthful empty states…"; browser check 9 | pass |
| 9 | Status: confirmation (ID, current, proposed, cancel, optional note), loading, no false success, refresh, persistence after refresh and re-correlation | `StatusControls.tsx`, `PATCH /incidents/{id}` | `StatusControls.test.tsx` (4 tests), `test_status_note_persists_and_unknown_incident_patch_is_404`, `test_status_update_and_validation`; browser checks 11–14 | pass |
| 10 | Cross-page integration: sidebar, Events, Data Quality, shared API layer, no broken navigation | `App.tsx`, `AppLayout.tsx`, `services/api.ts` | browser checks 4, 6–8 | pass |
| 11 | Professional, responsive, accessible UI (no new UI framework) | Tailwind + existing shadcn components; collapsible sidebar below `md` | browser check 15 (no horizontal overflow at 390 px) | pass |
| 12 | Minimal backend changes with regression tests; correlation thresholds and rules unchanged | `api/incidents.py`, `schemas/incidents.py`, `correlation/repository.py` | 5 new backend tests; rule version still `4a.1`; 50K precision 0.9937 / recall 0.9915 / 0 labelled false merges (unchanged) | pass |
| 13 | Performance at 50K: server-side pagination, bounded timeline rendering | 25–100 rows per page, timeline rendered 100 at a time | API 2.5–7.9 ms median; full page load to render about 135 ms (dev server) | pass |
| 14 | Automated tests | Vitest + Testing Library (dev only) | 33 frontend + 337 backend tests; `./evaluate.sh` runs both | pass |
| 15 | Real-browser verification | headless Chrome (puppeteer-core, outside the repo) | 20 of 20 checks passed | pass |
| 16 | Regression: `./evaluate.sh` and `./evaluate.sh --benchmark` | n/a | both passed (2026-11-22) | pass |
| 17 | Docker | unchanged Dockerfiles | not executed (Docker not installed) | pending |
| — | `GET /briefing` | implemented in Phase 5 (below) | see Phase 5 | pass |

## Phase 5: reasoning and operational briefings (requirements Sections 4.3, 6, 7, 8, 9 and 11)

Backend tests are in `backend/tests/test_briefing.py` (31), `test_reasoning.py` (21) and `test_config.py` (15). Frontend tests are in `frontend/src/pages/briefing/__tests__/BriefingPage.test.tsx` (11). Details: [briefing.md](briefing.md), [reasoning.md](reasoning.md), [benchmarks.md](benchmarks.md#phase-5-briefings-run-of-2026-11-22).

| # | Requirement | Implementation | Verification | Result |
|---|---|---|---|---|
| 2 | Structured findings: statement, category, event / incident / quality ids, confidence, uncertainty, missing information, reasoning, actions; fact vs observed vs inference vs conflict vs missing vs recommendation | `schemas/briefing.py`, `briefing/engine.py`, `reasoning/incident_reasoning.py` | `test_shift_briefing_has_every_required_part_and_is_grounded`, `test_incident_reasoning_classifies_statements`, `test_device_health_finding_and_grounded_recommendation` (sensor reports are `observed`) | pass |
| 3 | Deterministic extraction; optional AI narrative with validation; offline fallback; replaceable provider | `reasoning/narrative.py`, `reasoning/grounding.py` | `test_check_narrative_rejects_anything_not_in_the_grounded_content`, `test_unsupported_or_failed_model_output_falls_back_to_the_template`, `test_ollama_provider_unreachable_falls_back`, `test_briefing_uses_provider_and_rejects_fabrication_end_to_end` | pass (no real model exercised; stub providers only) |
| 4 | Transparent prioritization (severity, status, recency, evidence, confidence, conflicts, device failure, anomalies); severity untouched; duplicates add nothing; deterministic ties | `reasoning/prioritization.py` | `test_priority_ranking_is_explained_deterministic_and_never_changes_severity`, `test_priority_factors_and_tie_breaking`, `test_resolved_incidents_leave_the_priority_and_unresolved_lists` | pass |
| 5 | Shift, daily and custom briefings; UTC internally; configurable display time zone; all 14 content items | `briefing/periods.py`, `briefing/engine.py`, `api/briefing.py` | `test_daily_briefing_and_previous_shift_separation`, `test_night_shift_crosses_midnight_and_time_zone_is_applied`, `test_dst_day_has_23_hours`, structure test | pass |
| 6 | Evidence grounding: ids, counts, timestamps, devices and locations from the database; insufficient evidence stated; empty periods explained; "no reports" is not "safe" | `grounding.verify_references`, `empty_reason`, `missing` findings | `test_counts_come_from_the_database`, `test_empty_period_says_so_without_filler`, `test_verify_references_flags_fabricated_and_mismatched_ids`, `test_findings_never_list_the_same_event_twice`; benchmark (26 periods, all counts equal to SQL, every reference valid); browser checks 4–7 | pass |
| 7 | Temporal correctness: late, out-of-order, boundary, multi-day; occurrence vs ingestion vs detection vs current status; no duplicate counting; adjacent and overlapping windows | `period.time_basis`, half-open bounds, comparison cut to covered data | `test_shift_boundaries_are_half_open`, `test_adjacent_windows_add_up_and_overlaps_are_consistent`, `test_late_and_out_of_order_events_count_by_occurrence_time`, `test_repeated_submissions_are_not_counted_twice`, `test_new_versus_carried_over_incidents`, `test_default_period_is_the_latest_mostly_covered_shift` | pass |
| 8 | `GET /briefing` with mode, start, end, time zone and zone filter; structured output; validation | `api/briefing.py` (plus `/briefing/periods`, `/reasoning/incidents/{id}`) | `test_invalid_parameters_are_rejected_with_the_field` (8 cases), `test_unknown_mode_and_limit_are_validated`, `test_zone_filter_restricts_events_incidents_and_findings`, `test_periods_endpoint` | pass |
| 9 | Briefings page: period controls, summary, priority incidents, trends, device health, data quality, recommendations, confidence, evidence; navigation to incidents, events and quality issues; loading, empty and error states | `frontend/src/pages/briefing/` | `BriefingPage.test.tsx` (11); browser checks 1–12 (16 of 16 passed) | pass |
| 10 | Performance: demo, clean 50K and mixed 50K; latency, memory, correctness | indexed aggregates, bounded samples, no raw submissions loaded | benchmark: median 141 / 152 ms, max 427 / 422 ms; 186 MB peak; counts equal to SQL | pass |
| 11 | Automated tests (all listed areas) | n/a | 404 backend + 44 frontend tests | pass |
| 12 | CORS configuration fix (JSON list, comma-separated, empty, invalid, startup) | `core/config.py` (`NoDecode` + validator) | `test_config.py`: 7 format cases, 5 invalid cases, default, startup with either format applying CORS headers; browser run used the comma form | pass |
| 13 | Seven mandatory endpoints and the three workflows together | n/a | demo instance: every endpoint returned 200 or 201; quality finding → its events → incident timeline → briefing reference, and event → related incident → raw submission | pass |
| 14 | Documentation | README, briefing.md, reasoning.md, architecture.md, benchmarks.md, traceability.md, ingestion.md, `.env.example`, `evaluate.sh` | n/a | pass |
| 15 | Correlation accuracy not regressed | correlation untouched | 50K: precision 0.9937, recall 0.9915, 0 labelled false merges | pass |
| — | Docker | unchanged Dockerfiles (`tzdata` added to requirements) | not executed (Docker not installed) | pending |

## Phase 6: operations overview and platform integration (requirements Sections 7, 8, 9 and 11)

Details: [dashboard.md](dashboard.md). Browser checks were run in headless Chrome against a fresh database loaded with `scripts/load_demo.py` (real `POST /ingest` plus the background pipeline), and against a second, empty database.

| # | Requirement | Implementation | Verification | Result |
|---|---|---|---|---|
| 2 | Dashboard: operational summary, priority incidents, event activity, data quality, briefing preview; real values only; visible period; scopes labelled | `pages/dashboard/DashboardPage.tsx`, `GET /dashboard/summary` | `test_period_metrics_match_the_briefing_and_the_events_api`, `test_stored_totals_are_all_time_and_labelled`; frontend "shows the period…", "labels all-time totals…"; browser checks 1, 2, 2b, 2c | pass |
| 3 | Server-side aggregation; documented window; zone filter; no duplicated logic; no model dependency; tested | `services/briefing/dashboard.py` (reuses `BriefingBuilder`) | `test_dashboard.py` (12); benchmark counts vs SQL | pass |
| 3 | No "online" status inferred from silence | `device_health_note`; drops shown as `missing` | `test_quality_and_device_health_are_grounded`; frontend "never presents device health as confirmed healthy" | pass |
| 4 | Navigation: dashboard → incident → evidence; dashboard → quality issue → event; dashboard → briefing; briefing → incident → evidence; event → incident; quality issue → event | links plus `backTo` state ("Back to overview" / "Back to briefing") | frontend dashboard tests; browser checks 3–6c; `test_linked_lists_reproduce_the_dashboard_counts` | pass |
| 5 | UI consistency, desktop and narrow | shared state components, time-zone-labelled formatting everywhere, responsive grid | browser check 8 (all five pages at 390 px, no overflow); no console errors (missing favicon fixed) | pass |
| 6 | Time-zone and reporting consistency | `formatDateTime(value, tz)` with zone label; `zonedInputToIso` for custom briefing bounds (previously browser-local: fixed) | `format.test.ts` (4: Dubai, UTC, BST, DST change); `test_zone_filter_and_time_zone`; browser check 7 | pass |
| 7 | Honest presentation (observed / fact / inference / warning / recommendation; heuristics not probabilities; no "safe" claims) | basis badges, scope text, empty-state wording | `test_empty_database`, frontend empty and health tests | pass |
| 8 | Demo data: documented loading through the real pipeline; no silent insertion; usable empty state | `scripts/load_demo.py` (refuses a non-empty database), Getting-started card, `/events?import=1`, README *Demo walkthrough* | browser checks 9, 9b; script run (loaded 500; second run refused) | pass |
| 9 | Tests | n/a | 416 backend + 61 frontend | pass |
| 10 | Real browser verification | `verify6` script (outside the repo) | 22 of 22 | pass |
| 11 | Full regression and 50K responsiveness | `./evaluate.sh --benchmark` | passed; dashboard 140–166 ms (shift) / 440–444 ms (day); correlation unchanged; seven mandatory endpoints return 200/201 | pass |
| — | Docker | unchanged Dockerfiles | not executed (Docker not installed) | pending |
