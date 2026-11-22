# Architecture (Phase 6)

Zenith is a **modular monolith**: one FastAPI backend process and one React single-page app, backed by SQLite.

```text
Browser (React SPA) ──HTTP/JSON──▶ FastAPI ──SQLAlchemy──▶ SQLite (WAL)
   Dashboard (overview)              │
   Events page                       │
   Incidents page + investigation    │
   Briefings page                    │
   Data Quality page                 ├─ api/ingest.py   POST /ingest (JSON + streamed JSONL) → schedules analysis
                                     ├─ api/events.py   GET /events, /events/{id}, /events/facets,
                                     │                  /submissions, /batches
                                     ├─ api/quality.py  GET /quality-issues, /quality-issues/{id}, /summary,
                                     │                  POST /quality-issues/analyze
                                     ├─ api/incidents.py GET /incidents, /incidents/{id}, /summary,
                                     │                  POST /incidents/correlate, PATCH /incidents/{id}
                                     ├─ api/briefing.py GET /briefing, /briefing/periods, /reasoning/incidents/{id}
                                     ├─ api/dashboard.py GET /dashboard/summary (projection of the briefing engine)
                                     ├─ services/ingestion/
                                     │    records.py   parse + validate one record → PreparedRecord (pure)
                                     │    service.py   BatchIngestor: chunked transactional persistence,
                                     │                 duplicate classification
                                     │    jsonl.py     streaming line reader with a per-line memory bound
                                     │    queries.py   index-backed filtering and pagination
                                     ├─ services/quality/
                                     │    configuration.py  thresholds (ZENITH_QUALITY_*)
                                     │    rules/            17 read-only rules → IssueDraft (streamed queries)
                                     │    engine.py         run_analysis + background pipeline scheduler
                                     │    repository.py     idempotent fingerprint sync + issue queries
                                     └─ services/correlation/
                                          candidate_selection.py  bounded family queries + hash/time indexes
                                          clustering.py     seeds, strong links (constrained union), context leaves
                                          scoring.py        linkage / incident confidence, severity
                                          explanations.py   deterministic incident text
                                          repository.py     identity-preserving sync, status preservation

backend/datagen/    dataset generator + ground truth + integrity checks + quality evaluation
                    (tooling, never imported by app/)
scripts/            generate_events.py (CLI), benchmark_ingest.py (50K ingestion + quality tests)
```

## Ingestion flow

1. `POST /ingest` picks a channel. JSON bodies are read up to a size limit and parsed once. JSONL bodies are streamed line by line.
2. Each record becomes a `PreparedRecord` holding the preserved evidence (raw text, size, SHA-256, extracted `event_id`), errors or warnings, and the normalized values. This step is pure and touches no database.
3. `BatchIngestor` buffers records and persists each chunk of 1,000 in one transaction: it looks up existing `event_id`s, classifies each record (accepted, invalid, exact duplicate, conflicting duplicate), bulk-inserts the raw submissions and new events, and links them.
4. The response summarizes the batch and returns record-level results.

Two persistence layers ([ingestion.md](ingestion.md)):

- **`raw_submissions`**: append-only evidence of every record, whatever its outcome.
- **`events`**: one normalized row per external `event_id`, created by the first valid submission and never overwritten.

Phase 3 reads both layers without changing them: `raw_submissions` for duplicates and invalid records, `events` for everything else. Its findings live in their own tables (`quality_issues`, `quality_issue_evidence`, `quality_issue_facets`, `quality_runs`), linked to events and submissions by id. Phases 4–5 can use the quality issues as reliability inputs for correlation and briefings.

## Quality analysis flow

1. Each ingest request ends by calling `QualityScheduler.request()`. A background thread runs `run_analysis`, and concurrent requests coalesce into one follow-up run.
2. Every rule streams only the columns it needs, through indexed queries sorted by key and time. Rules never write.
3. `sync_issues` makes the stored issues equal to the findings, keyed by deterministic fingerprints (insert, update, keep, or remove), in one transaction.

See [data-quality.md](data-quality.md) for each rule.

## Correlation flow

After the quality analysis, the same background pipeline runs `run_correlation`. It loads bounded candidate families, builds incidents from notable seeds through strong identifier links (with merge constraints), attaches unambiguous context as leaves, renders deterministic explanations, and syncs incidents by core-event overlap. Incident ids and operator status survive re-runs, late evidence, splits and merges. Incidents reference events and submissions by id and never modify them. See [correlation.md](correlation.md).

## Backend layout

| Package | Responsibility |
|---|---|
| `app/core` | Settings (`config.py`), DB engine with SQLite pragmas and sessions (`database.py`), error envelope (`errors.py`) |
| `app/api` | HTTP routers: health, ingest, events/evidence, quality issues |
| `app/models` | `IngestBatch`, `RawSubmission`, `Event`, `QualityIssue*`, `QualityRun`, `Incident`, `IncidentEvidence`, `IncidentFacet`, `CorrelationRun`, plus a `UTCDateTime` column type |
| `app/schemas` | Event input schema, source registry and aliases, response models |
| `app/services/ingestion` | Validation, persistence, JSONL streaming, queries |
| `app/services/quality` | Rule-based data-quality engine (Phase 3) |
| `app/services/correlation` | Evidence-based incident correlation (Phase 4A), see [correlation.md](correlation.md) |
| `app/services/{reasoning,briefing}` | Empty. **Future phases.** |

## Conventions

- **Settings** come from `ZENITH_*` environment variables (or `backend/.env`).
- **DB sessions** are injected per request with `Depends(get_db)` and always closed. Blocking ingestion work runs in a thread pool so the event loop stays responsive.
- **Errors** are returned as `{"error": {"code", "message", "details?"}}`. Services raise `AppError`.
- **Times** are UTC-aware in Python and API output, and naive UTC in SQLite.

## Design decisions and trade-offs

| Decision | Why | Trade-off |
|---|---|---|
| Separate raw and normalized tables | Evidence (including invalid and duplicate records) must be preserved and traceable, while queries need clean typed columns | Roughly twice the storage |
| First valid submission wins; later ones are kept as `duplicate` with a field diff | Normalized data stays stable; conflicting re-sends become visible to quality detection instead of silently overwriting | A corrected re-send is not applied automatically. Phase 3 will surface it. |
| Invalid vs warning split | Structural defects are rejected; suspicious but interpretable data (delays, clock skew, low confidence, missing optional fields) is kept and flagged | Quality judgement is deferred to Phase 3 |
| Timestamps must carry an offset | A local time without an offset is ambiguous and would place events in the wrong shift | Producers that send naive local time are rejected |
| Free-text `event_type` and an open `payload` object | 15 heterogeneous sources and new event types need no schema change | Payload contents are not validated per source in Phase 2 |
| Chunked transactions (1,000 records) + streamed JSONL | Bounded memory (about 106 MB peak at 50K) and consistent layers per chunk | A crash mid-batch leaves earlier chunks committed (batch marked `failed`) |
| Offset pagination + composite `(filter, occurred_at, id)` indexes | Simple, deterministic, below 5 ms at 50K | Keyset pagination would scale better far beyond this size |
| SQLite (WAL) | No infrastructure; runs on any laptop; readers are not blocked by ingestion | Single writer; Postgres would be needed for multi-node deployments |
| Synchronous ingestion in the request | The client gets exact record-level results in one call (50K in about 10 s) | Very large uploads hold one HTTP request open |
| Full deterministic re-analysis after each ingest (background), synced by fingerprint | Cross-event findings stay consistent with all the evidence; re-runs never duplicate issues | About 5 s of background work per ingest at 50K events, regardless of batch size |
| Rules with explicit linking evidence only (plate, radar track, unambiguous badge/face pairing) | Avoids false conflicts between different people or vehicles | Conflicts without a shared identifier are reported as unresolved, not as conflicts |
| Poisson baseline + Bonferroni-style correction for spikes | Normal randomness across thousands of windows rarely triggers an alert | Small bursts at busy keys are not reported; baselines need history |
| Seeds + strong-identifier links + context-as-leaves (not connected components over weak edges) | Time and place alone never merge; weak chains cannot join two incidents | Events without identifiers attach only when unambiguous, which costs recall where similar incidents overlap |
| Identity by core-event overlap, not fingerprint | Incident ids and operator status survive late evidence, anchor changes, splits and merges | Overlap matching is a heuristic for split/merge ownership (largest overlap wins) |

## Frontend (Phase 4B)

The React SPA has one page per module, routed with React Router:

| Route | Page |
|---|---|
| `/events` | Events |
| `/data-quality` | Data Quality |
| `/incidents` | Incidents |
| `/incidents/:id` | Investigation |

- **State:** list state (filters, sort, page) lives in the URL query string. Detail panels (`?event=`, `?issue=`) are query parameters on the investigation route, so refresh and back navigation keep context.
- **Data:** all data comes from the typed client in `frontend/src/services/api.ts`. The UI performs no correlation or scoring. The event and quality-issue panels are shared between the Events, Data Quality and investigation pages.
- **Tests:** frontend tests run with Vitest and Testing Library in jsdom (`npm test`).

See [investigation-ui.md](investigation-ui.md).

## Reasoning and briefings (Phase 5)

```text
GET /briefing ─▶ briefing/periods.py   shift / day / custom → explicit UTC [start, end), time zones (zoneinfo)
             └▶ briefing/engine.py    indexed SQL aggregates over events, quality_issues, incidents (+ facets)
                   ├─ reasoning/prioritization.py  additive, explained priority score (severity never changed)
                   ├─ Poisson comparison with the same hours on previous days ("significant")
                   ├─ findings: basis (fact / observed / inference / conflict / missing / recommendation) + evidence ids
                   ├─ recommendation rules, overall confidence (heuristic)
                   ├─ reasoning/narrative.py   template summary; optional local model rewording (Ollama), validated
                   └─ reasoning/grounding.py   every event / incident / quality reference re-checked before returning
```

Both services are read-only. They never modify events, quality findings or incidents. No new storage was added: briefings are computed on request from the existing tables (about 0.15–0.45 s at 50K events). No model is needed. The optional provider is behind an interface, and any failure or ungrounded output falls back to the deterministic text. See [briefing.md](briefing.md) and [reasoning.md](reasoning.md).

## Operations overview (Phase 6)

`GET /dashboard/summary` runs the same `BriefingBuilder` as `GET /briefing` for the same period rules, so the two can never disagree. It then adds a small number of queries:

- all-time totals, labelled as such;
- the high/critical quality findings in the period.

The frontend Dashboard is the landing page. Every number links to the filtered page that reproduces it; `test_linked_lists_reproduce_the_dashboard_counts` checks this. All times carry a time-zone label, and custom briefing bounds are entered in the selected zone. See [dashboard.md](dashboard.md).

Navigation map:

```text
Dashboard ─┬─ metric cards / bars ─▶ Events · Incidents · Data Quality (same period filters)
           ├─ priority incident ─▶ Investigation ─▶ timeline event ─▶ raw submission ─▶ related incidents
           ├─ quality finding ─▶ quality panel ─▶ Open event ─▶ Events page (event panel)
           └─ briefing preview ─▶ Briefings (same period) ─▶ incident / event / quality evidence
```
