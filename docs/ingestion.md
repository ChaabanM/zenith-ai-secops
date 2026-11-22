# Ingestion, storage and retrieval

## Database schema

SQLite runs in WAL mode with `foreign_keys=ON` and `synchronous=NORMAL`. Tables are created at startup. Models: [`backend/app/models/ingestion.py`](../backend/app/models/ingestion.py).

```text
ingest_batches        one row per POST /ingest request
  id (batch-YYYYMMDDTHHMMSS-xxxxxx), channel (json|jsonl), filename,
  status (processing|completed|rejected|failed), created_at, completed_at,
  total_received, accepted, invalid, duplicates, exact_duplicates,
  conflicting_duplicates, with_warnings

raw_submissions       evidence layer: every submitted record, every outcome (append-only)
  id, batch_id → ingest_batches, position (1-based; JSONL line number),
  external_event_id (when extractable, even from malformed lines), raw_content,
  raw_size_bytes, raw_truncated, content_sha256 (of the full original bytes),
  ingested_at, status (accepted|invalid|duplicate), duplicate_kind (exact|conflicting),
  conflicting_fields (JSON), errors (JSON), warnings (JSON),
  normalized_event_id → events (accepted: the event created; duplicate: the event repeated)
  UNIQUE (batch_id, position); indexes on batch_id, external_event_id, status, normalized_event_id

events                normalized layer: one row per external event_id
  id, event_id UNIQUE, source_type, device_id, event_type, occurred_at, received_at,
  ingested_at, delay_seconds, latitude, longitude, zone, severity, confidence,
  payload (JSON), content_sha256 (of normalized fields), submission_id UNIQUE → raw_submissions
  indexes: (occurred_at,id), (severity,occurred_at,id), (source_type,occurred_at,id),
           (device_id,occurred_at,id), (zone,occurred_at,id)
```

Timestamps are stored as naive UTC with fixed microsecond precision, so they sort chronologically. The API returns them as UTC-aware values (`...Z`).

### Transactional consistency

Records are written in chunks (`ZENITH_INGEST_CHUNK_SIZE`, default 1,000). Each chunk is one transaction that inserts its raw submissions and new events and links them, so the two layers never disagree. A failure part-way through a batch keeps the chunks already committed as evidence and marks the batch `failed`. A concurrent batch that claims the same `event_id` between lookup and insert triggers a unique-constraint violation; the chunk is then rolled back and retried once against fresh state.

## Raw evidence

- Every record is stored, whatever its outcome: accepted, invalid, malformed, duplicate, or a duplicate with conflicting content.
- JSON request elements are stored as compact JSON with the original key order. JSONL lines are stored byte-for-byte (CRLF is tolerated). `content_sha256` hashes the original bytes.
- A request whose body is not JSON at all is stored as a single invalid submission in a `rejected` batch. The 400 response returns its `submission_id`.
- **Size limits:** a record larger than `ZENITH_MAX_RECORD_BYTES` (64 KiB) is invalid (`record_too_large`). Its first 64 KiB are kept as evidence with `raw_truncated=true`, along with its true size and hash. A JSON body is limited to `ZENITH_MAX_JSON_BODY_BYTES` (64 MiB), which returns 413. JSONL is streamed line by line with a per-line memory bound and no total limit.

## Duplicate handling (event ID uniqueness contract)

1. The **first valid** submission of an external `event_id` creates the normalized event.
2. Later valid submissions with the same `event_id`, in the same or any later request, are stored as raw submissions with status `duplicate`. They point to the existing event and **never modify it**.
   - `exact`: all normalized fields are identical. Key order, whitespace and alias spelling do not matter.
   - `conflicting`: at least one field differs. `conflicting_fields` lists which (for example `["confidence", "payload"]`).
3. Invalid submissions never claim an `event_id`. A later valid submission with that id is accepted.
4. Different `event_id`s are always kept as different events, even with the same device, location and timestamp. Deciding whether two observations describe the same real-world thing is left to correlation (Phase 4).

`GET /events/{id}` lists the duplicate submissions with their raw content.

## API

Interactive documentation is at `http://localhost:8000/docs`. Every error uses the envelope `{"error": {"code", "message", "details?"}}`.

### POST /ingest

| Body | Content-Type | Notes |
|---|---|---|
| Single event object | `application/json` | |
| JSON array of events | `application/json` | |
| `{"events": [ ... ]}` | `application/json` | batch wrapper |
| JSONL (one JSON object per line) | `application/x-ndjson` (or `application/jsonl`, or `?format=jsonl`) | streamed in bounded chunks; blank lines are skipped; malformed lines are recorded as invalid and do not stop later lines |

Query parameters: `filename` (recorded on the batch) and `format=json|jsonl` (overrides content-type detection).

| Status | Meaning |
|---|---|
| 201 | At least one new event was stored. The batch may still contain invalid and duplicate records. |
| 200 | Processed, but no new events: every valid record was a duplicate. |
| 400 | Request-level problem: body is not JSON (`malformed_json`, raw body preserved), empty body (`empty_body`), `[]` (`empty_batch`), or `events` is not an array (`invalid_batch_format`). |
| 413 | JSON body above the limit. Use JSONL instead. |
| 415 | Multipart upload. Send the file as the request body. |
| 422 | Every record was invalid. `error.details` holds the full summary below. |

Example, a mixed batch:

```bash
curl -s -X POST localhost:8000/ingest -H 'Content-Type: application/json' -d '[
  {"event_id":"evt-1","source_type":"radar","device_id":"radar-07","event_type":"speed_violation",
   "occurred_at":"2026-01-15T10:12:04Z","received_at":"2026-01-15T10:12:18Z","severity":"high","confidence":0.94,
   "location":{"latitude":25.2048,"longitude":55.2708,"zone":"Gate-A"},"payload":{"speed_kph":112}},
  {"event_id":"evt-2","source_type":"lpr","device_id":"anpr-int01","event_type":"plate_read","occurred_at":"2026-01-15T10:12:06Z"},
  {"event_id":"evt-3","source_type":"radar","device_id":"radar-07","event_type":"speed_violation","occurred_at":"15/01/2026 10:12","confidence":1.4},
  {"event_id":"evt-1","source_type":"radar","device_id":"radar-07","event_type":"speed_violation",
   "occurred_at":"2026-01-15T10:12:04Z","severity":"critical"}
]'
```

```json
{
  "batch_id": "batch-20261122T101500-3f9a2c",
  "status": "completed",
  "channel": "json",
  "filename": null,
  "total_received": 4,
  "accepted": 2,
  "invalid": 1,
  "duplicate_event_ids": 1,
  "exact_duplicates": 0,
  "conflicting_duplicates": 1,
  "with_warnings": 2,
  "duration_ms": 6.1,
  "records": [
    {"position": 1, "submission_id": 1, "event_id": "evt-1", "status": "accepted", "normalized_event_id": 1, "errors": [], "warnings": [], "duplicate_kind": null, "conflicting_fields": null},
    {"position": 2, "submission_id": 2, "event_id": "evt-2", "status": "accepted", "normalized_event_id": 2, "errors": [],
     "warnings": [{"field": "source_type", "code": "source_alias_normalized", "message": "'lpr' normalized to 'anpr'"},
                  {"field": "received_at", "code": "missing_received_at", "message": "received_at not provided; arrival delay unknown"}, "..."]},
    {"position": 3, "submission_id": 3, "event_id": "evt-3", "status": "invalid", "normalized_event_id": null,
     "errors": [{"field": "occurred_at", "code": "value_error", "message": "must be an ISO-8601 timestamp such as 2026-01-15T10:12:04Z"},
                {"field": "confidence", "code": "less_than_equal", "message": "Input should be less than or equal to 1"}], "warnings": []},
    {"position": 4, "submission_id": 4, "event_id": "evt-1", "status": "duplicate", "duplicate_kind": "conflicting",
     "conflicting_fields": ["received_at", "latitude", "longitude", "zone", "severity", "confidence", "payload"],
     "normalized_event_id": 1, "errors": [],
     "warnings": [{"field": "event_id", "code": "duplicate_conflicting", "message": "event_id already ingested; differs from the stored event in: ..."}, "..."]}
  ],
  "records_truncated": false,
  "quality_analysis": "scheduled"
}
```

`quality_analysis` is `scheduled` when background data-quality analysis is enabled (the default; see [data-quality.md](data-quality.md)), otherwise `disabled`.

`total_received = accepted + invalid + duplicate_event_ids` always holds. `records` lists every record when there are at most `ZENITH_MAX_RECORD_REPORTS` (1,000) of them. Beyond that it lists only invalid and duplicate records (`records_truncated: true`). The rest can be inspected through `GET /submissions?batch_id=...`.

JSONL import of a large file:

```bash
curl -s -X POST 'localhost:8000/ingest?filename=events_50000.jsonl' \
  -H 'Content-Type: application/x-ndjson' --data-binary @data/generated/events_50000.jsonl
```

### GET /events

| Parameter | Description |
|---|---|
| `page` (≥1), `page_size` (1–500, default 50) | Offset pagination. The response includes `total`, `total_pages`, `has_next`. |
| `severity`, `source_type`, `device_id`, `zone`, `event_type` | Exact match. Repeat the parameter or use commas for several values. `source_type` accepts aliases. |
| `event_id` | External event id. |
| `occurred_from` (inclusive), `occurred_to` (exclusive) | ISO-8601 with offset. |
| `sort` | `-occurred_at` (default), `occurred_at`, `-id` (ingestion order, newest first), `id`. Ties are always broken by `id`, so the order is deterministic across pages. |

Only the requested page is loaded. Filters and sorting use composite indexes ending in `(occurred_at, id)`. Invalid filter values return 422 with `details: [{"field", "message"}]`.

### Other endpoints

| Endpoint | Purpose |
|---|---|
| `GET /events/{id}` | Normalized event, `payload`, `submission` (raw evidence, batch, position, hashes, validation warnings), `duplicate_submissions`, and `quality_issues` (the data-quality findings involving this event). `id` is the internal id from `GET /events`. |
| `GET /events/facets` | Distinct sources, severities, zones and devices with counts, plus the `occurred_at` range. Used by the UI filters. |
| `GET /submissions` | Raw evidence in ingestion order. Filters: `batch_id`, `status`, `event_id`, `normalized_event_id`. |
| `GET /submissions/{id}` | One raw submission, including invalid and malformed ones. |
| `GET /batches`, `GET /batches/{id}` | Ingestion requests with their outcome counts. |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ZENITH_INGEST_CHUNK_SIZE` | 1000 | Records per database transaction |
| `ZENITH_MAX_RECORD_BYTES` | 65536 | Maximum size of a single record; larger records are invalid and their evidence is truncated |
| `ZENITH_MAX_JSON_BODY_BYTES` | 67108864 | Maximum size of a JSON (non-JSONL) request body |
| `ZENITH_MAX_RECORD_REPORTS` | 1000 | Maximum record-level results returned inline |

## Data-quality API (Phase 3)

Details of each rule are in [data-quality.md](data-quality.md).

### GET /quality-issues

| Parameter | Description |
|---|---|
| `page`, `page_size` (1–500, default 50) | Pagination with `total`, `total_pages`, `has_next` |
| `issue_type` | `duplicate_submission`, `invalid_record`, `delayed_event`, `out_of_order_arrival`, `timestamp_conflict`, `location_conflict`, `conflicting_sensor_results`, `repeated_device_events`, `event_spike`, `device_offline`, `device_degraded`, `sensor_failure`, `device_recovered`, `device_health_warning`, `low_confidence_detection`, `uncorrelatable_event` (repeat or comma-separate) |
| `severity`, `rule_id` | Exact match, multi-value |
| `source_type` (aliases accepted), `device_id`, `zone` | Issues concerning any of the values |
| `event_id` / `event` | Issues whose evidence includes this external event id / internal event id |
| `occurred_from`, `occurred_to` | Overlap with the issue's time span (ISO-8601 with offset) |
| `sort` | `-severity` (default: most severe, then most recent), `-occurred_at`, `occurred_at`, `-id`; ties broken by id |

```bash
curl -s 'localhost:8000/quality-issues?issue_type=conflicting_sensor_results&zone=Gate-A&page_size=2'
```

```json
{
  "items": [{
    "id": 56, "issue_type": "conflicting_sensor_results", "subtype": "identity", "severity": "high",
    "title": "Badge of EMP-1354 used while face recognition matched EMP-1186 at Gate-A",
    "description": "The person recognised at the door is not the holder of the credential that opened it. ...",
    "rule_id": "SNS-002", "rule_version": "1.0", "confidence": 0.9,
    "occurred_from": "2026-01-14T19:45:15Z", "occurred_to": "2026-01-14T19:45:16Z", "time_basis": "occurred_at",
    "event_count": 2, "submission_count": 2, "devices": ["acs-gate-a", "frs-gate-a"], "zones": ["Gate-A"],
    "sources": ["access_control", "face_recognition"], "status": "open",
    "first_detected_at": "...", "last_detected_at": "..."
  }],
  "page": 1, "page_size": 2, "total": 1, "total_pages": 1, "has_next": false
}
```

### GET /quality-issues/{id}

The summary fields plus:

- `detection_reason`, `uncertainty` and `missing_evidence`;
- `details`: structured facts, for example `differences` with stored vs resubmitted values, `distance_km` and `implied_speed_kph`, `expected_in_window` and `threshold`, `recovered_at` and `downtime_seconds`;
- `evidence[]`: each item has a `role`, an `external_event_id`, an `event` (normalized event summary, or null for rejected records) and a `submission` (batch, position, status, validation errors). `evidence_total` and `evidence_truncated` cap the list at 500 items.

The original content of any submission is available at `GET /submissions/{id}`.

### GET /quality-issues/summary

Returns `total` and counts by type, severity, rule, device, zone and source. It also returns `last_run` (trigger, status, duration, analysed counts, inserted/updated/unchanged/removed, per-rule timings) and `analysis_pending`.

### POST /quality-issues/analyze

Runs the full analysis synchronously and returns the run record. The run is idempotent, so a second run on unchanged data reports `inserted = updated = removed = 0`.

## Incidents API (Phase 4A)

See [correlation.md](correlation.md#api) for `GET /incidents`, `GET /incidents/{id}`, `GET /incidents/summary`, `POST /incidents/correlate` and `PATCH /incidents/{id}`. `GET /events/{id}` is unchanged. Use `GET /incidents?event=<id>` to find the incidents that include an event. See [briefing.md](briefing.md#api) for `GET /briefing`, `GET /briefing/periods` and `GET /reasoning/incidents/{id}`, and [dashboard.md](dashboard.md#api) for `GET /dashboard/summary`.
