# 50K performance tests

Command (repository root): `backend/.venv/bin/python scripts/benchmark_ingest.py`, or `./evaluate.sh --benchmark`.

For each test (A clean, B mixed), the script:

1. Generates the dataset in a separate process (timed; peak RSS from `wait4`).
2. Starts the real API (`uvicorn app.main:app`) on a fresh SQLite file, with background quality analysis off so pure ingestion is measured.
3. Streams the JSONL file to `POST /ingest` over HTTP. There are no direct SQL inserts, so every record goes through validation.
4. Checks the response summary **and every stored submission** (status, duplicate kind, duplicate target, preserved event id) against the ground truth.
5. Walks all pages of `GET /events` (500 per page) to check there are no losses, no repeats and the order is stable.
6. Measures query latency: one warm-up, then the median and maximum of 5 requests over HTTP from the same machine.
7. **Phase 3 and 4A:**
   - Runs `POST /quality-issues/analyze` and runs it again to prove idempotency.
   - Measures issue-query latency.
   - Checks every evidence reference against `events` and `raw_submissions`, and checks that no raw evidence was lost.
   - Measures issue storage with SQLite `dbstat`.
   - Compares the findings with the ground truth.
   - Runs `POST /incidents/correlate` twice (idempotency), measures incident-query latency, checks every incident evidence link against `events` / `raw_submissions`, checks that source events are unchanged (fingerprint before and after), and compares incidents with the ground truth.
   - For the mixed test only, ingests again with the background pipeline **on** (quality analysis, then correlation: the normal workflow) to measure its impact.

There are no pass/fail time thresholds, because the requirements define none. The numbers below come from one run.

## Environment

| | |
|---|---|
| Hardware | Apple M5, 10 cores, 16 GB RAM (laptop, no GPU used) |
| OS | macOS 26.6.2 (arm64) |
| Software | Python 3.12.10, SQLite 3.51.0, FastAPI 0.115.6, Pydantic 2.10.4, SQLAlchemy 2.0.36, uvicorn 0.32.1 (single worker) |
| Settings | defaults: chunk size 1,000, WAL mode, `synchronous=NORMAL`, quality thresholds as documented |
| Data | generator 1.1.0, seed 42, 50,000 submissions over 3 days |

## Ingestion (run of 2026-11-22)

| Metric | Test A: clean 50K | Test B: mixed 50K |
|---|---|---|
| Expected → observed accepted | 50,000 → 50,000 | 47,500 → 47,500 |
| Invalid | 0 → 0 | 1,500 → 1,500 |
| Duplicate event IDs (exact / conflicting) | 0 → 0 | 1,000 → 1,000 (476 / 524) |
| Per-record verification vs ground truth | passed | passed |
| Full traversal (`GET /events`, 500/page) | 50,000 unique, ordered (1.83 s) | 47,500 unique, ordered (1.86 s) |
| Generation time (in-process / whole CLI incl. write + integrity check) | 1.11 s / 3.43 s | 1.11 s / 3.27 s |
| Generator peak memory | 292 MB | 292 MB |
| JSONL size | 23 MB | 23 MB |
| Ingestion time (HTTP, streamed) | 10.21 s | 10.20 s |
| Throughput | 4,898 submissions/s | 4,901 submissions/s |
| Database size (raw evidence + events + quality issues) | 94.2 MB | 96.8 MB |
| Server peak memory (ingestion + two analyses) | 139 MB | 156 MB |

### Event query latency (ms, median / max)

| Query | Clean | Mixed |
|---|---|---|
| Page 1 / middle / last (50 per page) | 3.6 / 4.0 / 4.4 | 3.7 / 4.0 / 4.4 |
| `severity=critical` | 2.7 | 2.7 |
| `source_type=anpr` | 2.9 | 2.9 |
| `device_id=acs-gate-b` | 2.8 | 2.6 |
| `zone=Gate-A` | 2.8 | 2.7 |
| 8-hour `occurred_at` window | 2.9 | 2.9 |
| Combined (source + 2 severities + zone + 2-day window) | 6.1 | 6.5 |
| `GET /events/{id}` (with evidence and linked quality issues) | 2.0 | 1.8 |
| `GET /events/facets` | 21 | 20 |

## Phase 3: data-quality analysis

| Metric | Test A: clean 50K | Test B: mixed 50K |
|---|---|---|
| Analysis time (HTTP / engine) | 4.60 s / 4.57 s | 5.18 s / 5.13 s |
| Records analysed | 50,000 events, 50,000 submissions | 47,500 events, 50,000 submissions |
| Issues found | 4,493 | 6,999 |
| Re-run (idempotency) | 4.36 s: inserted 0, updated 0, removed 0, unchanged 4,493 | 4.28 s: inserted 0, updated 0, removed 0, unchanged 6,999 |
| Evidence links | 9,577 | 14,078 |
| Broken event / submission references | 0 / 0 | 0 / 0 |
| Issues without evidence, duplicate fingerprints | 0, 0 | 0, 0 |
| Raw submissions after analysis | 50,000 (none lost) | 50,000 (none lost) |
| Issue storage (tables + indexes) | 7.8 MB | 12.6 MB |
| Server peak RSS after analysis | 138 MB | 156 MB (107 MB with ingestion only in Phase 2) |

**Impact on ingestion (mixed, background analysis on):** ingestion took 10.18 s, unchanged from 10.20 s with analysis off. The background run took 5.0 s and all 6,999 issues were queryable **15.3 s after the upload started**. Peak server RSS was 159 MB.

### Issues by type

| Issue type | Clean | Mixed |
|---|---|---|
| invalid_record | 0 | 1,500 |
| delayed_event | 1,315 | 1,315 |
| low_confidence_detection | 1,262 | 1,262 |
| uncorrelatable_event | 1,033 | 1,038 |
| duplicate_submission | 0 | 1,000 |
| timestamp_conflict | 200 | 200 |
| out_of_order_arrival | 157 | 157 |
| conflicting_sensor_results | 149 | 149 |
| event_spike | 61 | 62 |
| location_conflict | 61 | 61 |
| repeated_device_events | 55 | 55 |
| device_offline / degraded / sensor_failure / recovered | 50 / 50 / 50 / 50 | 50 / 50 / 50 / 50 |

The "clean" profile has no invalid or duplicate submissions, but it still contains scenarios B–G and I–L (delays, conflicts, device failures, low confidence), so those findings are expected. Precision and recall against the ground truth are in [data-quality.md](data-quality.md#ground-truth-evaluation-50k-mixed-seed-42).

### Rule timings (mixed, ms)

| SPK-001 | ORD-001 | LOC-001 | SNS-001 | REP-001 | DUP-001 | LCF-001 | VAL-001 | SNS-002 | TSC-002 | DLY-001 | UNC-001 | HLT-001 | SNS-003 | UNC-003 | TSC-001 | UNC-002 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2,129 | 652 | 275 | 227 | 198 | 128 | 118 | 100 | 94 | 83 | 52 | 50 | 41 | 33 | 24 | 18 | 1 |

### Quality query latency (ms, median)

| Query | Clean | Mixed |
|---|---|---|
| List page 1 / last page (50 per page) | 3.5 / 3.3 | 3.7 / 3.6 |
| `issue_type=delayed_event` | 3.4 | 3.5 |
| `severity=high,critical` | 3.9 | 6.2 |
| `device_id=…` / `zone=…` (facet filters) | 5.6 / 5.4 | 7.4 / 6.6 |
| 8-hour time window | 4.7 | 5.6 |
| `GET /quality-issues/summary` | 4.8 | 7.8 |
| `GET /quality-issues/{id}` (largest spike, with evidence) | 3.6 | 3.8 |

## Observations

- JSONL ingestion is streamed and persisted in 1,000-record chunks, so memory does not grow with file size. Analysis adds about 50 MB at peak, because findings are collected before the single sync transaction.
- Each filtered page costs one `COUNT` plus one indexed page query. Offset pagination stays below 5 ms at the last page for 50K rows. Much larger tables would call for keyset pagination.
- The spike rule dominates analysis time: four dimensions, each streamed once in sorted order with two window sizes evaluated in the same pass.

## Phase 4A: incident correlation

Run of 2026-11-22 (same environment; full run via `./evaluate.sh --benchmark`).

| Metric | Test A: clean 50K | Test B: mixed 50K |
|---|---|---|
| Correlation time (HTTP) | 1.31 s | 1.27 s |
| Stages: candidate selection / clustering / explanations / sync | 695 / 223 / 53 / 304 ms | 632 / 213 / 102 / 296 ms |
| Candidate rows loaded (scalar fields only) | 39,406 | 37,483 |
| Active incidents | 718 | 718 |
| Re-run (idempotency) | 1.00 s: created 0, updated 0, merged 0, obsoleted 0, unchanged 718 | 0.97 s: created 0, updated 0, merged 0, obsoleted 0, unchanged 718 |
| Evidence links | 3,257 | 3,255 |
| Broken event/submission references, duplicate links, events in two active incidents, incidents without evidence | 0, 0, 0, 0 | 0, 0, 0, 0 |
| Source events modified | no (fingerprint unchanged) | no (fingerprint unchanged) |
| Incident storage (tables + indexes) | 4.1 MB | 4.2 MB |
| Server peak RSS after correlation | 170 MB | 182 MB |

**Normal workflow (mixed, background pipeline on):** ingestion took 10.16 s (unchanged), the background quality analysis 5.0 s and correlation 1.3 s. All 6,999 issues and 718 incidents were queryable **16.5 s after the upload started**. Peak RSS was 191 MB.

### Incident query latency (ms, median, mixed)

| List page 1 | `category` | `severity=critical,high` | `zone` | `device_id` | 8-hour window | summary | detail | detail (60-event cyber incident) |
|---|---|---|---|---|---|---|---|---|
| 4.6 | 4.8 | 5.7 | 5.7 | 5.2 | 5.2 | 3.3 | 2.6 | 3.8 |

Ground-truth results (precision 0.994, recall 0.992, 0 labelled false merges) are detailed in [correlation.md](correlation.md#ground-truth-evaluation).

## Phase 4B: investigation dashboard (run of 2026-11-22)

`./evaluate.sh --benchmark` was re-run after the Phase 4B changes, in the same environment.

| Metric | Clean 50K | Mixed 50K |
|---|---|---|
| Ingestion | 10.68 s (4,680/s) | 10.63 s (4,701/s) |
| Quality analysis / re-run | 4.99 s, 4,493 issues / 0 changes | 5.32 s, 6,999 issues / 0 changes |
| Correlation / re-run | 1.33 s, 718 incidents / 0 changes | 1.28 s, 718 incidents / 0 changes |
| Correlation vs ground truth | precision 0.9937, recall 0.9915, 0 labelled false merges | same |
| Verification (records, evidence links, unchanged source events) | passed | passed |
| Server peak RSS / database | 182 MB / 98.3 MB | 183 MB / 100.9 MB |

The background pipeline took 10.73 s for ingestion, and issues and incidents were queryable after 17.43 s. That is within run-to-run variation of the Phase 4A figure of 16.5 s; the Phase 4B changes do not touch this path.

Investigation UI and API latencies, measured on a copy of the mixed 50K database (median of 5 after one warm-up), are in [investigation-ui.md](investigation-ui.md#verification). The new `priority` sort costs 7–8 ms per page. Browser rendering takes about 135 ms from a full page load to a rendered list or a rendered 61-event investigation (Vite dev server).

## Phase 5: briefings (run of 2026-11-22)

`./evaluate.sh --benchmark` was run after the Phase 5 changes, in the same environment. For each dataset, a briefing was generated over HTTP for 13 periods: the three days, the nine shifts, and the default. Each was measured with one warm-up and three runs. Each briefing's `events_total` and `incidents_active` were compared with direct SQL counts, and every referenced event (id and external `event_id`), incident and quality issue was looked up in the database.

| Metric | Clean 50K | Mixed 50K |
|---|---|---|
| Briefing latency, median / max over 13 periods | 141 / 427 ms | 152 / 422 ms |
| Full day (14 Jan): events / incidents / latency | 16,395 / 275 / 427 ms | 15,595 / 275 / 422 ms |
| Default period (evening shift, 14 Jan): events / incidents / latency | 4,859 / 70 / 138 ms | 4,625 / 70 / 145 ms |
| Counts vs SQL | all 13 match | all 13 match |
| References checked: events / incidents / quality issues | 5,353 / 1,475 / 1,027, all valid | 5,395 / 1,475 / 1,065, all valid |
| Source events modified by briefing generation | no (fingerprint unchanged) | no (fingerprint unchanged) |
| Server peak RSS (whole run, including ingestion, analysis and correlation) | 186 MB | 186 MB |

Other results from the same run:

| Metric | Clean 50K | Mixed 50K |
|---|---|---|
| Ingestion | 10.58 s | 10.51 s |
| Quality analysis | 4.90 s | 5.23 s |
| Correlation | 1.24 s | 1.23 s |
| Correlation vs ground truth | precision 0.9937, recall 0.9915, 0 labelled false merges (unchanged from Phase 4A) | same |

With the background pipeline (mixed), issues and incidents were queryable 17.24 s after the upload started.

On the demo dataset (500 submissions), a briefing takes about 20 ms (median of 5).

The full-day briefing is the slowest because the "busiest" and "significant" comparisons group all of a day's events three times: the period itself and two comparison days.

## Phase 6: operations overview (run of 2026-11-22)

`./evaluate.sh --benchmark` was run after the Phase 6 changes. `GET /dashboard/summary` was timed for the default (latest shift) and the latest day, as the median of 5 after a warm-up. Its period event count and all-time event count were checked against SQL, and its references were verified.

| Metric | Clean 50K | Mixed 50K |
|---|---|---|
| Dashboard, default shift (median / max) | 140 / 143 ms (4,859 events) | 166 / 205 ms (4,625 events) |
| Dashboard, latest day (median / max) | 440 / 482 ms (16,395 events) | 444 / 486 ms (15,595 events) |
| Counts vs SQL, references | match, all verified | match, all verified |
| Briefings, 13 periods (median / max) | 139 / 421 ms | 150 / 431 ms |
| Correlation vs ground truth | precision 0.9937, recall 0.9915, 0 labelled false merges | same |
| Server peak RSS | 187 MB | 188 MB |

On the demo dataset, the dashboard takes 24 ms (default) and 26 ms (day).

## Phase 7A: final audit (run of 2026-11-22)

`./evaluate.sh --benchmark` was run in the working copy **after** the audit fix (comparison-window coverage). `./evaluate.sh` was also run in a fresh clone from GitHub. Both passed.

| Metric | Clean 50K | Mixed 50K |
|---|---|---|
| Generation (in-process / whole CLI) | 1.12 s / 3.89 s | 1.16 s / 3.93 s |
| Ingestion over HTTP | 10.50 s (4,760/s) | 10.59 s (4,723/s) |
| Every record vs ground truth | passed | passed |
| Full `GET /events` traversal | 2.01 s | 1.90 s |
| Event page 1 / last page (median) | 3.69 / 4.53 ms | 3.62 / 4.40 ms |
| Quality analysis / re-run | 4.89 s, 4,493 issues / 0 changes | 5.14 s, 6,999 issues / 0 changes |
| Correlation / re-run | 1.32 s, 718 incidents / 0 changes | 1.27 s, 718 incidents / 0 changes |
| Correlation vs ground truth | precision 0.9937, recall 0.9915, 0 labelled false merges | same |
| Briefings, 13 periods (median / max); counts vs SQL; references | 138 / 425 ms; match; all valid | 149 / 427 ms; match; all valid |
| Dashboard, default / day (median) | 147 / 438 ms | 164 / 452 ms |
| Server peak RSS / database | 188 MB / 98.3 MB | 190 MB / 100.9 MB |

With the background pipeline (mixed), issues and incidents were queryable 17.14 s after the upload started. Run-to-run variation across Phases 4A–7A was about ±0.5 s for ingestion and ±30 ms for the dashboard.

## Phase 7B: after the presentation changes (run of 2026-11-22)

The presentation-only changes did not move any measurement beyond run-to-run variation.

| Metric | Clean 50K | Mixed 50K |
|---|---|---|
| Ingestion | 10.25 s | 10.66 s |
| Quality analysis (re-run: 0 changes) | 4.77 s | 5.22 s |
| Correlation (re-run: 0 changes) | 1.24 s | 1.23 s |
| Correlation vs ground truth | precision 0.9937, recall 0.9915, 0 labelled false merges | same |
| Briefings, 13 periods (median / max) | 134 / 411 ms | 157 / 428 ms |
| Dashboard, default / day (median) | 140 / 443 ms | 167 / 467 ms |
| Server peak RSS | 185 MB | 189 MB |
