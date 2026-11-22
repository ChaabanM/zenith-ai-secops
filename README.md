# Zenith AI-Native Security Operations Platform

A security operations platform that runs on a laptop and needs no GPU, no paid API key and no network connection. It ingests events from 15 kinds of security sensor and finds data-quality problems. It groups related events into explainable incidents and writes evidence-backed operational briefings for a shift or a day.

- **Repository:** https://github.com/ChaabanM/zenith-ai-secops (public)
- **Demo video:** https://youtu.be/t5Tcaf50BOk
- [demo walkthrough](#demo-walkthrough-under-5-minutes).

## Contents

1. [The problem](#the-problem)
2. [Features and workflows](#features-and-workflows)
3. [Architecture](#architecture)
4. [Technology stack](#technology-stack)
5. [System requirements](#system-requirements)
6. [Run locally](#run-locally)
7. [Run with Docker](#run-with-docker)
8. [Configuration](#configuration)
9. [Demo data](#demo-data)
10. [Dataset generator and 50K evaluation](#dataset-generator-and-50k-evaluation)
11. [API](#api)
12. [Tests](#tests)
13. [Verification results](#verification-results)
14. [Evidence grounding and explainability](#evidence-grounding-and-explainability)
15. [AI and reasoning approach](#ai-and-reasoning-approach)
16. [Design decisions and trade-offs](#design-decisions-and-trade-offs)
17. [Known limitations](#known-limitations)
18. [Demo walkthrough (under 5 minutes)](#demo-walkthrough-under-5-minutes)
19. [Documentation index](#documentation-index)
20. [AI Technologies and Development Tools](#ai-technologies-and-development-tools)

## The problem

A security operations team receives a continuous stream of events from cameras, plate readers, face recognition, access control, perimeter sensors, radar, fleet GPS, network logs and more. The data is messy:

- records are duplicated, malformed, late or out of order;
- sensors contradict each other;
- devices fail silently.

A single real-world situation shows up as many unrelated-looking records.

Zenith helps an operator and a manager answer these questions:

- What is happening?
- Which events belong together?
- How reliable is the data?
- What needs attention now?
- What happened during this shift?

Every answer stays traceable to the original evidence.

## Features and workflows

**Three workflows in one application:**

| Workflow | What it does | Where |
|---|---|---|
| **Data quality** | Seventeen deterministic rules find duplicates, malformed records, delays, out-of-order arrival, conflicting timestamps, locations and sensors, repeated alerts, volume spikes, device-health problems, low-confidence detections and events that cannot be linked. Each finding has a reason, its evidence and any uncertainty. | Data Quality page, `GET /quality-issues` |
| **Incident investigation** | Related events are grouped into incidents, each with a title, category, severity, heuristic confidence, a chronological timeline, an explanation of every link, conflicts, missing information, uncertainty and advisory actions. Operators set the status. | Incidents page, `GET /incidents`, `GET /incidents/{id}` |
| **Operational briefing** | Shift, daily or custom-period briefings in any time zone. They cover priority and unresolved incidents, repeated activity, device health, trends, data quality, conflicts and uncertainty, recommendations and overall confidence. Every finding links to its events. | Briefings page, `GET /briefing` |

**Supporting features:**

- **Dashboard:** an operations overview for the latest shift or day. Every number opens the filtered list that reproduces it.
- **Events page:** import JSON or JSONL files, filter and paginate, and inspect any event down to its raw submission.
- **Background processing:** after each ingest, the quality analysis and then correlation run automatically.

## Architecture

The platform is a modular monolith: one FastAPI process, one React single-page app, and SQLite.

```text
Browser (React SPA) ──HTTP/JSON──▶ FastAPI ──SQLAlchemy──▶ SQLite (WAL)
 Dashboard · Events · Data Quality    api/        HTTP routers (ingest, events, quality, incidents, briefing, dashboard, health)
 Incidents · Briefings                services/   ingestion → quality (17 rules) → correlation → reasoning / briefing
```

Data flows in five steps:

1. `POST /ingest` validates each record.
2. Every submission is stored as raw evidence (including invalid records and duplicates); valid records also become normalized events.
3. A background task runs the quality analysis.
4. Correlation then runs. Both steps are idempotent, and re-runs preserve incident IDs and operator status.
5. Briefings and the dashboard are computed on request from the stored data.

Full description: [docs/architecture.md](docs/architecture.md).

```text
backend/app/        api/, core/ (settings, database, errors), models/, schemas/, services/{ingestion,quality,correlation,reasoning,briefing}
backend/datagen/    dataset generator, ground truth, integrity checks, evaluation (tooling; never imported by app/)
backend/tests/      pytest suite
frontend/src/pages/ dashboard/, events/, quality/, incidents/, briefing/ (each with __tests__/)
data/samples/       committed demo dataset and its ground truth (500 submissions)
data/generated/     generated datasets and benchmark output (git-ignored)
scripts/            load_demo.py, generate_events.py, benchmark_ingest.py, evaluate_quality.py
evaluate.sh         the project's test command
```

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI 0.115, Pydantic 2.10, pydantic-settings 2.7, SQLAlchemy 2.0, SQLite (WAL mode), pytest 8 |
| Frontend | React 19.1, TypeScript 5.8, Vite 6.3, Tailwind CSS 4.1, shadcn/ui, React Router 7.6, Vitest with Testing Library (development only) |
| Containers | Docker Compose. The backend image is `python:3.12-slim`; the frontend image is built with `node:22-alpine` and served by `nginx:1.27-alpine`. |

## System requirements

**Local run:**
- Python 3.11 or newer.
- Node.js 20.19+ or 22.12+, with npm.
- The final audit ran the backend tests on Python 3.11.11 and 3.12.10, and the frontend on Node 24.16.

**Docker run:** Docker Desktop, or Docker Engine with the Compose v2 plugin.

**Hardware:**
- An ordinary laptop is enough; no GPU is needed.
- The 50K benchmark peaked at about 190 MB of server memory.

**Network:**
- Needed only to install dependencies.
- At run time the application makes no external calls: the frontend loads no external resources, and the default reasoning provider is deterministic.

## Run locally

Use two terminals, both starting from the repository root.

**Terminal 1: backend** (http://localhost:8000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # optional; every setting has a default
uvicorn app.main:app --reload --port 8000
```

**Terminal 2: frontend** (http://localhost:5173)

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. The header shows **Backend connected** when the frontend can reach `GET /health`. The database (`backend/zenith.db`) starts empty; load the demo data as described under [Demo data](#demo-data).

## Run with Docker

```bash
docker compose up -d --build       # build both images and start in the background
docker compose ps                  # backend should show "healthy"
curl -s http://localhost:8000/health
python3 scripts/load_demo.py       # optional, from the host: loads the demo data into the container
```

| Service | Host port | Notes |
|---|---|---|
| `frontend` | http://localhost:5173 | The built app served by nginx on container port 80, with a fallback for app routes. It starts once the backend reports healthy. |
| `backend` | http://localhost:8000 | uvicorn. Its `HEALTHCHECK` calls `GET /health`. It runs with `ZENITH_ENVIRONMENT=docker` and the database at `/data/zenith.db`. |

The browser calls the API directly at `http://localhost:8000`, which is set as a frontend build argument. CORS allows `http://localhost:5173` and `http://127.0.0.1:5173`.

**Before starting:** stop any local Vite dev server (`npm run dev`) or local backend first. Docker publishes ports 5173 and 8000 on the host, and a process already using either port stops the container from starting. Run `lsof -i :5173` (or `:8000`) to see what holds a port.

**Everyday commands:**

| Command | Effect | Data |
|---|---|---|
| `docker compose up -d` | Start, or recreate after changes; add `--build` after code changes | kept |
| `docker compose ps` / `docker compose logs -f backend` | Status, health and logs | kept |
| `docker compose restart` | Restart both containers | kept (verified) |
| `docker compose stop` / `docker compose start` | Stop and start without removing containers | kept |
| `docker compose down` | Remove the containers and network | kept: the `backend-data` volume remains |
| `docker compose down -v` | Remove the containers **and the database volume** | **deleted permanently** |

> **Warning:** `docker compose down -v` deletes the `backend-data` volume, which holds the SQLite database: every ingested event, quality finding, incident and operator status. Use plain `docker compose down` to keep the data.

### Docker verification status

The setup was **manually verified on macOS with Docker Desktop** before submission:

| Check | Result |
|---|---|
| Backend and frontend images build | passed |
| `docker compose up -d` starts both services | passed |
| Backend container reports `healthy` | passed |
| Frontend container starts | passed |
| `GET http://localhost:8000/health` | passed: `status` "ok", `environment` "docker", database connected |
| React app loads at http://localhost:5173 | passed |
| Dashboard shows real stored data | passed |
| `docker compose restart` restarts both containers | passed |
| Backend returns to `healthy` after restart | passed |
| Data persists after restart | passed: the reporting period still showed 54 events, 2 incidents with activity, 2 unresolved incidents and 12 data-quality findings |

During installation, port 5173 was already in use by a local Vite dev server. Stopping that server before `docker compose up` fixed it.

**Not verified with Docker:**
- other operating systems (Linux, Windows);
- other Docker versions or engines;
- running `./evaluate.sh` or the 50K benchmarks inside containers;
- `docker compose down -v` and a rebuild from scratch;
- deployment beyond a single machine.

The automated tests and benchmarks were run with the local setup.

## Configuration

Backend settings come from environment variables with the `ZENITH_` prefix. They can also be set in `backend/.env` (see [`backend/.env.example`](backend/.env.example)). Every setting has a default.

| Variable | Default | Purpose |
|---|---|---|
| `ZENITH_ENVIRONMENT` | `development` | Environment label returned by `/health` (`docker` in Compose) |
| `ZENITH_DEBUG` | `false` | FastAPI debug mode |
| `ZENITH_DATABASE_URL` | `sqlite:///./zenith.db` | SQLAlchemy database URL (Compose: `sqlite:////data/zenith.db`) |
| `ZENITH_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed origins, comma-separated or as a JSON list. Empty means none. Each must be `http(s)://…` or `*`. |
| `ZENITH_INGEST_CHUNK_SIZE` | `1000` | Records per database transaction |
| `ZENITH_MAX_RECORD_BYTES` | `65536` | Maximum size of one record. Larger records are invalid, and their evidence is truncated. |
| `ZENITH_MAX_JSON_BODY_BYTES` | `67108864` | Maximum JSON body. JSONL is streamed with no total limit. |
| `ZENITH_MAX_RECORD_REPORTS` | `1000` | Record-level results returned inline per ingest |
| `ZENITH_QUALITY_AUTO_ANALYZE` | `true` | Run the quality analysis in the background after every ingest |
| `ZENITH_QUALITY_*` | see [docs/data-quality.md](docs/data-quality.md#thresholds-and-settings) | Quality-rule thresholds |
| `ZENITH_CORRELATION_AUTO_CORRELATE` | `true` | Run correlation after each background quality analysis |
| `ZENITH_CORRELATION_*` | see [`configuration.py`](backend/app/services/correlation/configuration.py) | Correlation windows and thresholds |
| `ZENITH_BRIEFING_TIMEZONE` | `UTC` | Display and shift time zone, as an IANA name such as `Asia/Dubai`. Storage is always UTC. |
| `ZENITH_BRIEFING_*` | see [`configuration.py`](backend/app/services/briefing/configuration.py) | Shifts, comparison windows, significance thresholds, list sizes |
| `ZENITH_REASONING_PROVIDER` | `deterministic` | `deterministic` (templates only) or `ollama` (optional local-model rewording of the summary; see [AI and reasoning approach](#ai-and-reasoning-approach)) |
| `ZENITH_REASONING_*` | see [`configuration.py`](backend/app/services/reasoning/configuration.py) | Ollama URL (`http://127.0.0.1:11434`), model, timeout; prioritization weights |

The frontend reads `VITE_API_BASE_URL` (default `http://localhost:8000`) from `frontend/.env.local`. In Docker it is a build argument.

**Database persistence:**
- **Locally:** the database is the SQLite file `backend/zenith.db`, with WAL side files.
- **In Docker:** it lives in the named volume `backend-data`, which survives restarts and `docker compose down`.
- **Schema:** tables are created at startup; there are no migrations.
- **Git:** database files are git-ignored.

## Demo data

The committed demo dataset is `data/samples/demo_events.json`: 500 submissions covering all 15 sources and every scenario type. Its ground truth is in `demo_ground_truth.json`. Nothing is loaded automatically: an empty database shows "No events stored yet".

There are three ways to load it. All of them go through the real `POST /ingest` pipeline.

1. **Script:** `python3 scripts/load_demo.py [--api http://localhost:8000]`. It needs only the Python standard library.
   - It waits for the background analysis and correlation, then prints the counts: 92 quality findings and 16 incidents.
   - It refuses to run if the database already contains events; `--force` overrides this.
2. **UI:** go to **Events → Import events**, choose `data/samples/demo_events.json` and click **Ingest**.
   - The result shows 500 received, 475 accepted, 15 invalid and 10 duplicate IDs (5 exact, 5 conflicting), with record-level errors.
3. **curl:**

   ```bash
   curl -s -X POST 'http://localhost:8000/ingest?filename=demo_events.json' \
     -H 'Content-Type: application/json' --data-binary @data/samples/demo_events.json
   ```

The event format, the 15 source categories and their aliases are documented in [docs/event-schema.md](docs/event-schema.md):

> cctv, anpr, face_recognition, mobile_phone_detection, seatbelt_detection, traffic_violation, radar, access_control, perimeter_intrusion, device_health, operator_report, emergency_alarm, gps_fleet, network_security, weather_environmental

## Dataset generator and 50K evaluation

The generator is deterministic and seeded. It produces a fictional site with every required data problem (scenarios A–L) and a separate ground-truth file. The production code never reads that ground truth; a test enforces this.

```bash
backend/.venv/bin/python scripts/generate_events.py --count 50000 --seed 42
curl -s -X POST 'http://localhost:8000/ingest?filename=events_50000.jsonl' \
  -H 'Content-Type: application/x-ndjson' --data-binary @data/generated/events_50000.jsonl
backend/.venv/bin/python scripts/evaluate_quality.py   # quality precision/recall against the ground truth
./evaluate.sh --benchmark                              # clean and mixed 50K end to end (about 3 minutes)
```

The Events page also accepts `.jsonl` files and streams the file body without parsing it in the browser.

Details: [docs/dataset.md](docs/dataset.md) and [docs/benchmarks.md](docs/benchmarks.md).

## API

- **Interactive documentation** (while the backend runs): Swagger UI at http://localhost:8000/docs, ReDoc at http://localhost:8000/redoc, and the schema at http://localhost:8000/openapi.json.
- **Reference with examples:** [docs/ingestion.md](docs/ingestion.md), [docs/correlation.md](docs/correlation.md) and [docs/briefing.md](docs/briefing.md).
- **Errors:** every error uses one envelope, `{"error": {"code": "...", "message": "...", "details": ...}}`.

**The seven mandatory endpoints (requirements Section 8) are all implemented:**

| Endpoint | Purpose |
|---|---|
| `POST /ingest` | A single event, a JSON array, `{"events": [...]}` or streamed JSONL. Accepts partial batches and returns record-level results. |
| `GET /events` | Paginated events with filters: `severity`, `source_type`, `device_id`, `zone`, `event_type`, `event_id`, `occurred_from`, `occurred_to`, `sort` |
| `GET /quality-issues` | Paginated quality findings with filters: `issue_type`, `severity`, `source_type`, `device_id`, `zone`, `event_id`, time range, `rule_id`, `sort` |
| `GET /incidents` | Paginated incidents with filters: `severity`, `status`, `category`, `zone`, `device_id`, `source_type`, `event_id`, time range, `include_inactive`, `sort` (`priority` puts unresolved incidents first) |
| `GET /incidents/{id}` | The full incident: title, category, severity and confidence with reasons, status, chronological timeline of original events with link explanations, devices, zones, conflicts, missing information, uncertainty, recommended actions and related quality issues |
| `GET /briefing` | Takes `mode=shift\|day\|custom`, `date`, `shift`, `start`/`end`, `tz` (IANA), `zone` and `limit`. With no parameters it covers the latest shift with stored data. Returns structured findings with evidence, recommendations, confidence and a verified evidence index. |
| `GET /health` | Health check. Returns 503 with `status: "degraded"` when the database is unavailable. |

**Additional endpoints:**
- `GET /events/{id}` and `GET /events/facets`;
- `GET /submissions[/{id}]` and `GET /batches[/{id}]` (raw evidence);
- `GET /quality-issues/{id}`, `GET /quality-issues/summary` and `POST /quality-issues/analyze`;
- `GET /incidents/summary`, `POST /incidents/correlate` and `PATCH /incidents/{id}` (operator status and note);
- `GET /briefing/periods`;
- `GET /dashboard/summary`;
- `GET /reasoning/incidents/{id}`.

## Tests

```bash
./evaluate.sh                  # backend tests, demo ground-truth integrity, quality vs ground truth, frontend tests, type-check and build
./evaluate.sh --skip-frontend  # without Node.js
./evaluate.sh --benchmark      # all of the above, plus both 50K benchmarks (about 3 minutes)
cd frontend && npm test        # frontend component tests only
```

**Backend: 421 pytest tests**
- **Ingestion:** validation for every source, field and error path; malformed JSON and JSONL; duplicates; evidence preservation; late and out-of-order events.
- **Generator:** reproducibility and ground-truth integrity.
- **Quality rules:** every rule, with adversarial negatives.
- **Correlation:** related and unrelated groups against the ground truth.
- **Repeat runs:** idempotency, and incident IDs and operator status are preserved.
- **Briefings:** periods, time zones and DST, grounding, prioritization, and the fallback from a fabricating model.
- **Dashboard:** counts match the briefing and every linked list.
- **Configuration:** CORS parsing.
- **Wording:** explanations stay provisional.

**Frontend: 77 Vitest and Testing Library tests**
- the operations overview;
- incident list, investigation and status controls;
- the briefing page, including collapsible sections and print;
- the Events table;
- the quality detail panel;
- time-zone formatting.

Details are in the *Tests* section of each document in `docs/`.

## Verification results

All results come from the **synthetic generated datasets**: the 500-record demo set and the seeded 50K sets, scored against their own ground truth. **They are not measurements from a real deployment.**

| Check | Result |
|---|---|
| Backend tests | 421 passed |
| Frontend tests, type-check and production build | 77 passed; clean |
| Real-browser checks (headless Chrome, Phase 7B) | 26 of 26 passed: navigation, evidence, conflicts, print, 390 px layouts, no console errors |
| Evidence-chain audit | 13 of 13: SHA-256 of every raw submission; submission → event → quality issue → incident → briefing |
| `./evaluate.sh --benchmark` (clean and mixed 50K) | both passed |
| Correlation against the ground truth (50K, pairwise) | precision 0.9937, recall 0.9915, **0 merges of event pairs labelled as must-stay-separate** |
| Seven mandatory API endpoints | implemented and exercised by the API audit |
| Docker | manually verified on macOS (see [Docker verification status](#docker-verification-status)) |

**50K benchmark summary.** These figures are from the latest run, after Phase 7B.

Test machine:
- Apple M5 laptop (10 cores, 16 GB) running macOS 26.6;
- Python 3.12 and SQLite 3.51;
- one uvicorn worker, outside Docker.

| | Clean 50K | Mixed 50K |
|---|---|---|
| Accepted / invalid / duplicate | 50,000 / 0 / 0 | 47,500 / 1,500 / 1,000 (476 exact, 524 conflicting) |
| Every record matches the ground truth | yes | yes |
| Ingestion over HTTP (streamed JSONL) | 10.25 s | 10.66 s |
| Quality analysis (idempotent re-run: 0 changes) | 4.77 s | 5.22 s |
| Correlation (idempotent re-run: 0 changes) | 1.24 s | 1.23 s |
| `GET /briefing`, 13 periods (median / max) | 134 / 411 ms | 157 / 428 ms |
| `GET /dashboard/summary`, default shift / latest day (median) | 140 / 443 ms | 167 / 467 ms |
| Server peak memory | 185 MB | 189 MB |

**From the Phase 7A audit run** (2026-11-22):
- Event list pages answered in 3.6–4.5 ms (median).
- A full `GET /events` traversal took 1.9–2.0 s.
- The database held about 98–101 MB, including evidence, issues and incidents.
- Quality analysis found 4,493 issues (clean) and 6,999 (mixed); correlation formed 718 incidents.
- Briefing counts matched SQL, and every reference was valid.

Run-to-run variation is about ±0.5 s for ingestion. Methodology and per-phase history: [docs/benchmarks.md](docs/benchmarks.md).

## Evidence grounding and explainability

- **Original evidence is never altered.** Every submission is stored exactly as received, with its SHA-256, including invalid records and duplicates. Normalized events keep the producer's `event_id`. From any incident, quality finding or briefing statement, the UI opens the source event and its raw record.
- **Every statement is labelled as one of these kinds:**
  - fact (database count);
  - sensor report;
  - inference;
  - conflict;
  - missing information;
  - recommendation.

  It carries the IDs of the events, incidents and quality findings behind it.
- **References are re-verified.** Every reference in a briefing or the dashboard is checked against the database before it is returned (`grounding.all_references_verified`), and any failure is listed.
- **Uncertainty is never hidden:**
  - Conflicts are shown side by side, without choosing a winner.
  - Missing information and uncertainty are never collapsed.
  - Low sensor confidence is flagged, and a value the sensor did not report is shown as "not reported", never as zero.
  - Identity pairing by time and place is described as provisional ("not verified to be the same person").
  - A device without health reports is never called healthy.
- **Scores are heuristic.** Incident confidence, rule confidence scores, priority and briefing confidence are documented weighted scores, not calibrated probabilities, and the UI says so.

Details: [docs/reasoning.md](docs/reasoning.md) and [docs/investigation-ui.md](docs/investigation-ui.md).

## AI and reasoning approach

The requirements ask for AI where it adds value, with a deterministic fallback. Each technique is named for what it is:

| Technique | Used for |
|---|---|
| Deterministic rules | Quality detection, correlation links, recommendations |
| Heuristic scoring | Incident confidence, priority, briefing confidence |
| Statistics | A Poisson comparison with the same hours on previous days, for "significantly above or below the usual level" and volume spikes |
| Template language generation | Every briefing sentence and the executive summary, filled from database values |
| Optional local language model | Rewording of the executive summary only, through [Ollama](https://ollama.com), when `ZENITH_REASONING_PROVIDER=ollama` is set. The output is accepted only if it introduces no identifiers or numbers that are not in the grounded content; otherwise the template text is used. |

> **No language model produced any result reported in this repository.** The default provider is `deterministic`. The Ollama adapter has been tested only with stub providers and an unreachable server, never with a real model. The API and UI always say which text is shown (`executive_summary.generated_by`).

Limitations:
- The grounding check catches new identifiers and numbers, but not changes in meaning.
- The rules and thresholds are not calibrated on real sensor data.

Details: [docs/reasoning.md](docs/reasoning.md).

## Design decisions and trade-offs

| Decision | Trade-off |
|---|---|
| Separate raw-evidence and normalized tables | Evidence is preserved and traceable, at about twice the storage |
| First valid submission wins; later re-sends are kept as duplicates with a field diff | Normalized data stays stable, but a corrected re-send is not applied automatically |
| Reject structural defects; keep suspicious data and flag it | Delayed, skewed or low-confidence data stays usable; judging its quality is left to the quality rules |
| Timestamps must carry an offset | Prevents wrong-shift placement; producers that send local time without an offset are rejected |
| SQLite (WAL) in a modular monolith | No infrastructure, runs on any laptop; one writer at a time |
| Full re-analysis after each ingest, synced by fingerprint | Findings stay consistent with all the evidence; about 5 s of background work at 50K |
| Correlation from seeds plus strong-identifier links, with context attached only as leaves | Time and place alone never merge incidents; ambiguity is resolved by not linking, which costs some recall |
| Templates by default; a model may only reword, and is validated | Fully reproducible and offline; the wording is plainer than a free-form model's |

The full table: [docs/architecture.md](docs/architecture.md#design-decisions-and-trade-offs).

## Known limitations

- **Heuristics, not probabilities.**
  - Quality rules and correlation scores are documented heuristics, not calibrated on real sensor data.
  - "Significant" trends are Poisson screening, not proof.
  - Comparisons use a window only if stored data covers at least 80% of it.
- **Correlation favours separation.** It links only through identifiers or unambiguous context, so similar overlapping incidents at one site can stay split (151 false splits at 50K).
  - Identity pairing by time and place is provisional.
  - Location checks need a fully read plate.
- **Status has no history.** Briefings use the current operator status. Each incident has a single replaceable status note and no audit trail.
  - There is no authentication, so no operator identity is recorded.
- **Scale.**
  - SQLite allows one writer at a time; concurrent batches are safe but serialized.
  - Each analysis re-processes the whole database.
  - Ingestion is synchronous within the request: 50K records take about 10 s.
  - Offset pagination would need keyset pagination at millions of rows.
- **Schema.** Payload contents are not validated per source. Tables are created at startup, without migrations.
- **No real language model tested.** The optional Ollama provider has been tested only with stubs, and its check does not catch changes in meaning.
- **UI limitations.**
  - The dashboard offers the latest shift or day; other periods are on the Briefings page.
  - Pages do not poll for new data.
  - The Events table scrolls sideways on narrow screens.
  - Collapsed sections always print.
- **Docker coverage.** Docker was verified manually on macOS only (see [the list of what was not verified](#docker-verification-status)).

Each document in `docs/` has its own *Known limitations* section.

## Demo walkthrough (under 5 minutes)

Before you start, the app should be running, from [Run locally](#run-locally) or [Run with Docker](#run-with-docker), with the demo data loaded (`python3 scripts/load_demo.py`).

1. **Open the dashboard** (http://localhost:5173). It shows the latest shift with data: event counts, priority incidents, event activity, data quality, device health and a briefing preview. Every number opens the list that reproduces it.
2. **Explain the ingestion pipeline.** On **Events → Import events**, a batch is validated record by record. The import summary shows:
   - invalid records, with their reasons;
   - exact and conflicting duplicates;
   - accepted events, stored with their raw submission.

   The quality analysis and correlation then run in the background. (On an already loaded database, describe the flow, or import the demo file again: the already stored events are reported as duplicates, and nothing is overwritten.)
3. **Inspect a data-quality finding.** On **Data Quality**, filter by type or severity and open a finding, such as an identity conflict. Show:
   - the rule's reason;
   - the heuristic rule score;
   - the uncertainty;
   - the evidence events.

   Then select **Open event**.
4. **Open a correlated incident.** Go to Dashboard → *Highest-priority unresolved incidents* → an incident. Show:
   - the chronological timeline;
   - how each link was established;
   - conflicts side by side;
   - missing information and uncertainty;
   - the advisory actions.

   Optionally, change the status; you are asked to confirm first.
5. **Inspect the original evidence.** Select an event ID on the timeline. The panel shows:
   - the normalized event;
   - the payload;
   - provenance with the SHA-256;
   - the raw submission exactly as received;
   - the incidents it belongs to.
6. **View an operational briefing.** Dashboard → *Open full briefing*, or **Briefings**. Choose a shift, day or custom period, a time zone and a zone. Every finding is labelled and links to its events, incidents and quality findings. Print it if needed.
7. **Explain uncertainty and grounding.** Point out:
   - the labels on each statement (fact, sensor report, inference, conflict, missing, recommendation);
   - the verified evidence index;
   - the deterministic-template note on the summary;
   - "not reported" sensor confidence;
   - that scores are heuristic, not probabilities.

## Documentation index

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Architecture, flows, design decisions and trade-offs |
| [docs/event-schema.md](docs/event-schema.md) | Event schema, mandatory and optional fields, invalid vs suspicious, the 15 sources and their aliases |
| [docs/ingestion.md](docs/ingestion.md) | Database schema, raw evidence, duplicates, API reference with examples, pagination and filters |
| [docs/data-quality.md](docs/data-quality.md) | Quality rules, thresholds, uncertainty, re-processing, precision and recall against the ground truth |
| [docs/correlation.md](docs/correlation.md) | Correlation algorithm, scoring, conflicts, idempotency, ground-truth results |
| [docs/investigation-ui.md](docs/investigation-ui.md) | Incident overview and investigation workflow, evidence classes, status controls |
| [docs/briefing.md](docs/briefing.md) | Briefing time windows, content, API, UI, performance |
| [docs/reasoning.md](docs/reasoning.md) | Rules, heuristics, statistics and language generation; grounding checks; the optional model and its fallback |
| [docs/dashboard.md](docs/dashboard.md) | Operations overview: period, scopes, panels, navigation, demo loading |
| [docs/dataset.md](docs/dataset.md) | Generator usage, scenarios A–L, ground truth |
| [docs/benchmarks.md](docs/benchmarks.md) | 50K methodology and results for every phase |
| [docs/traceability.md](docs/traceability.md) | Requirement → implementation → verification matrix |

## AI Technologies and Development Tools

The platform uses deterministic rules, heuristic scoring, and statistical methods for data-quality detection, incident correlation, and operational analysis.

Operational briefings are generated using evidence-grounded templates. An optional Ollama integration supports local language-model processing, while the default configuration operates without an external AI service, paid API, or GPU.

AI-assisted development tools were used during implementation. The system was validated through:

- Automated backend and frontend test suites.
- Ground-truth integrity checks.
- Record-by-record verification of 50,000-event datasets.
- Browser-based functional testing.
- Manual inspection of the user interface.

The main frameworks and dependencies are documented in the [Technology Stack](https://github.com/ChaabanM/zenith-ai-secops#technology-stack) section.
