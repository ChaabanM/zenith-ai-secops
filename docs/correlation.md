# Incident correlation (Phase 4A)

Code: [`backend/app/services/correlation/`](../backend/app/services/correlation/). The engine is deterministic and explainable, with no AI or LLM. It groups related events into incidents and keeps unrelated events apart. It never reads the generator's ground truth (a test checks that `app/` never references it).

| Module | Role |
|---|---|
| `candidate_selection.py` | Loads only correlation-relevant events, as scalar columns plus specific payload fields (no raw submissions, no full payloads), and builds hash + time-sorted indexes |
| `constraints.py` | Negative evidence: full-plate check, distance and implied speed, vehicle-detail contradictions, low confidence |
| `scoring.py` | Link types and linkage confidence, incident confidence, severity helpers |
| `clustering.py` | Evidence-gated incident construction (builders, constrained union, contextual leaves) |
| `explanations.py` | Deterministic incident text: observations, inferences, conflicts, missing information, uncertainty, actions |
| `repository.py` | Identity-preserving sync, status preservation, queries |
| `engine.py` | Orchestration, run log, process-wide lock |
| `configuration.py` | Thresholds (`ZENITH_CORRELATION_*`) |

## Algorithm

### 1. Candidate selection (bounded)

Ten source families are loaded with one indexed query each. Only the payload fields correlation uses are extracted (for example `plate_number` and `vehicle_make` for plate-bearing sources, `credential_id` and `door_id` for access control). Routine families are filtered out in SQL: heartbeats, shift notes, and CCTV motion events without attributes. Lookups go through in-memory hash maps keyed by identifier (plate, credential, zone, radar device + track, source IP + account, alarm input point), with values sorted by event time. Every lookup is a hash access plus a binary search over a bounded window, so events are **never compared pairwise across the dataset**. At 50K events about 29K rows of small tuples are loaded.

### 2. Seeds: only notable evidence can start an incident

| Builder | Seed | Category |
|---|---|---|
| Device health | A health episode (consecutive reports about one **affected** device with gaps ≤ 6 h) containing *offline* or *sensor failure* | `device_failure` |
| Perimeter | A fence cut, climb or beam-break alarm, or a high/critical perimeter alert | `perimeter_intrusion` |
| Access attempts | ≥ 3 denied attempts with **the same credential** at one zone, gaps ≤ 5 min | `unauthorized_access` |
| Authentication burst | ≥ 10 failed logins from **the same source address for the same account** on one device, gaps ≤ 2 min | `cyber_security` |
| Vehicle pass | An enforcement event (traffic violation, phone or seatbelt detection) linked to **another device** through a strong vehicle identifier | `traffic_enforcement` |
| Identity conflict | A confident face match (≥ 0.8) and a granted badge forming the **only** badge use and **only** face capture at one door within 3 s, naming different people | `unauthorized_access` |
| Impossible travel | The same fully read plate seen by two devices > 1.5 km apart at > 200 km/h implied speed (≤ 10 min) | `fleet_anomaly` if GPS is involved, otherwise `suspicious_activity` |
| Standalone alerts | An active non-test emergency alarm, a door forced open, a high/critical network alert, or an operator report with high/critical priority, when not already part of an incident | `emergency`, `unauthorized_access`, `cyber_security`, `suspicious_activity` |

**Routine observations never start an incident.** That includes plate reads, positions, heartbeats, granted badges, face matches, single denials, single violations and low-severity radar measurements. A single traffic violation with no corroborating sensor remains an enforcement record, not a security incident.

### 3. Strong links (core members)

Core members join only through a **shared reliable identifier or an explicit reference**, inside a bounded window:

| Link type | Evidence | Linkage confidence |
|---|---|---|
| `explicit_reference` | Violation cites the radar track (`radar_device_id` + `radar_track_id`, closest radar event within ±1 h); alarm panel input names the fence segment; operator report lists `referenced_event_ids` | 0.95 |
| `same_affected_device` | Health reports about the same affected device in one episode | 0.95 |
| `same_plate_same_site` | Same **fully read** plate at the same zone within 120 s | 0.90 |
| `same_credential` | Same credential in a continuous attempt sequence | 0.90 |
| `same_source_and_account` | Same source IP and account in one burst (plus a success within 10 min) | 0.90 |
| `same_door` | Door forced open on the attempted door within 5 min, assigned to the sequence whose last attempt most recently precedes it | 0.80 |
| `same_identifier_conflict` | Same plate in incompatible places (the contradiction is the evidence) | 0.80 |
| `plausible_movement` | Same fully read plate at another site within 45 min, > 1 km away, implied speed ≤ 150 km/h | 0.75 |
| `unambiguous_pairing` | Badge and face as above (time and place only, so it is used only to surface a conflict) | 0.70 |

**Vehicle passes** use a *constrained* union-find over these edges. Edges are processed strongest first, and a union is **rejected** in three cases:

- the two components carry **different plates** (two vehicles, for example a radar track id reused by another car);
- the merged span would exceed **1 h**;
- any pair of located observations across the two components implies **impossible travel**.

Partial or unreadable plates are never identifiers. The same plate at the same site more than 120 s apart (a later pass) is a separate incident.

### 4. Contextual evidence attaches leaves only

Some evidence has no identifier: camera detections, unmatched faces, operator reports without references, and the degraded output of a failing device. It is *proposed* to an incident when zone, time and event type fit:

- **perimeter:** a person detected within 60 s, or a camera verification within 120 s;
- **access:** loitering within 5 min, or an unmatched face within 90 s of the last attempt;
- **vehicle pass:** a CCTV vehicle with the same colour and category within ±10 s;
- **reports:** a report category that fits the incident type (`access_violation` → access, `perimeter` → perimeter);
- **device failure:** low-confidence output (< 0.55) of the failing device between the first problem report and the outage.

Three rules prevent **transitive over-merging**:

- A proposal is attached **only if exactly one incident proposes it**. Otherwise it stays unattached and is counted as ambiguous.
- Attached context is a **leaf**: it never recruits further events and cannot link two incidents.
- **Low-confidence detections (< 0.6) are never attached as context**, so weak evidence does not become identification because something happened nearby. The failing device's own degraded output is the documented exception, with the lowest linkage confidence (0.5).

Vague reports, for example `suspicious_activity` with no references, attach only through explicit references. Otherwise they become an incident only if their priority is high or critical.

### 5. One incident per event

Builders run in a fixed order: health, perimeter, access, authentication, vehicle passes, identity, travel, standalone alerts, reports, then contextual resolution. An event already in an incident is not reused. If an impossible-travel contradiction involves an event that already belongs to an incident, the contradicting observation is added to **that** incident as a conflict instead of creating a second one.

### 6. Negative evidence (summary)

| Negative evidence | Effect |
|---|---|
| Different plates | Vehicle components never merge |
| Impossible travel | Blocks merges; surfaced as a conflict or a fleet/suspicious incident |
| Different subjects at one door | Different credentials are different sequences. Badge vs face is a *conflict*, never a merge of identities. |
| Insufficient temporal overlap | Bounded windows per link type; maximum pass span 1 h; episode gaps |
| Shared location or device only | Never a strong link: context attaches as a leaf only when unambiguous, so two same-site incidents stay separate |
| Ambiguous context (two candidate incidents) | Left unattached |
| Low-confidence detection | Never used as context or as an identifier |

## Confidence (three different quantities, none calibrated)

1. **Source detection confidence** is the sensor's `confidence` on the event. It is shown per timeline entry as `source_confidence` and never modified.
2. **Linkage confidence** is the fixed value per link type from the table above, × 0.7 when the link carries a conflict.
3. **Incident confidence** is a heuristic score:
   `0.5 × mean core linkage + 0.4 × mean core detection confidence (missing = 0.85) + 0.05 per additional independent source type (max 0.10) − 0.10 per conflict (max 0.30) − 0.10 for a single uncorroborated event`, clamped to [0.05, 0.99].
   The level is **high** at ≥ 0.75, **medium** at ≥ 0.50, otherwise **low**. Every incident states its `confidence_reason`. Scores rank evidence strength; **they are not probabilities**.

## Severity

Severity is the **highest severity among strongly linked (core) events**. Contextual leaves and duplicate submissions never raise it. Adjustments, each stated in `severity_reason`:

- **device failure:** at least high while it lasted; **critical** if no recovery has been reported;
- **authentication burst:** high when a success follows, otherwise medium;
- **identity conflict or impossible travel:** medium (unverified contradictions);
- **weak evidence:** capped at **medium** when every strongly linked detection is low-confidence.

Duplicates are not events (Phase 2), so they cannot inflate severity or corroboration. Each incident reports `duplicate_submissions_ignored`.

## Conflicts, uncertainty and quality integration

- Conflicts found by the engine are stored on the evidence link (`conflict`) and in the incident's `conflicts` list. Examples: radar vs camera time or speed, vehicle make or colour mismatch, impossible travel, badge vs face, alarm vs camera "no activity". Both records are kept; neither is assumed correct.
- Phase 3 findings on the incident's events are used as **annotations**, never as exclusions:
  - conflict findings are added (or cited as corroboration when the engine found the same contradiction);
  - delayed or out-of-order findings become "timeline uses event time" notes;
  - "cannot be reliably linked" flags become an uncertainty note saying which link type was used.
  `GET /incidents/{id}` lists the related quality issues, computed on read; nothing is copied.
- `uncertainty` lists low-confidence detections, context-only links and quality notes. `missing_information` states what is absent (no camera confirmation, no recovery report, unregistered credential, …).
- Explanations are deterministic templates filled with event values. Sections: **observations** (verified, what sensors reported), **inferences** (what the engine concluded), **conflicts**, **missing information**, **uncertainty**, and **recommended actions** (advisory only; nothing is triggered automatically).

## Storage

| Table | Contents |
|---|---|
| `incidents` | Computed: `category`, `title`, `severity` (+ rank, reason), `confidence` (+ level, reason), `summary`, `linkage_explanation`, `observations`, `inferences`, `conflicts`, `missing_information`, `uncertainty`, `recommended_actions`, `details` (entities, link-type counts, ignored duplicates, quality issue ids), `first/last_occurred_at`, `event_count`, `rule_version`, `correlation_state` (active/merged/obsolete), `superseded_by_id`, `lineage`, `detected_at`, `updated_at`, `content_sha256`. **Operator-maintained:** `status` (open, acknowledged, investigating, resolved, false_positive), `status_note`, `status_updated_at` |
| `incident_evidence` | `incident_id`, `event_pk` → events, `external_event_id`, `submission_id` → raw submission, `role`, `link_type`, `is_core`, `linkage_explanation`, `linkage_confidence`, `conflict`. Unique `(incident_id, event_pk)`, so there are no duplicate links. Source events are referenced, never copied or modified. |
| `incident_facets` | Zone, device and source per incident, indexed for filtering |
| `correlation_runs` | Trigger, status, timings per stage, counts (created/updated/unchanged/merged/obsoleted), builder statistics |

## Reprocessing, identity and status

- **Workflow:** the background pipeline after every ingest runs the quality analysis and **then** correlation, coalesced and serialized. `POST /incidents/correlate` runs correlation on demand. A process-wide lock prevents concurrent runs from overwriting each other.
- **Full rebuild, stable identity:** each run recomputes all incidents deterministically. The sync then matches new groups to existing active incidents **by overlap of core events** (largest overlap first, ties to the lowest id), not by fingerprint. An incident therefore keeps its **id, `detected_at` and operator status** when late or out-of-order evidence extends it or changes its anchor.
- **Unchanged content** (same hash) is not rewritten, so a re-run on unchanged data reports `created = updated = merged = obsoleted = 0`. **Changed content** replaces the computed fields and evidence links in one transaction; `status`, `status_note` and `status_updated_at` are never written by the engine.
- **Split:** the part with the largest overlap keeps the id. Other parts become new incidents with `lineage.split_from`.
- **Merge:** when all core events of an incident now belong to another, the absorbed incident is kept with `correlation_state = merged` and `superseded_by_id`, and **its own status stays on its row**. The survivor lists it in `merged_incident_ids`. Hidden by default; `include_inactive=true` shows it.
- **Obsolete:** an incident no longer produced (for example after a threshold change) is kept with `correlation_state = obsolete`. Nothing is ever deleted.

## API

| Endpoint | Purpose |
|---|---|
| `GET /incidents` | Pagination, filters `severity`, `status`, `category`, `zone`, `device_id`, `source_type` (aliases accepted), `event_id` / `event`, `occurred_from` / `occurred_to` (overlap), `include_inactive`. Sort `-severity` (default), `priority` (unresolved first, then severity and recency; added in Phase 4B), `-last_occurred_at`, `first_occurred_at`, `-confidence`, `-id`; ties broken by id. |
| `GET /incidents/{id}` | All Section 4.2 fields plus a `timeline` (chronological by event time, with original `event_id`, `event_pk`, `submission_id`, role, link type, `link_description`, core flag, linkage confidence and explanation, source confidence, `low_source_confidence` flag, conflict), `low_source_confidence_threshold`, `quality_issues`, `merged_incident_ids` and `lineage` |
| `GET /incidents/summary` | Counts by category, severity, status and confidence level, inactive count, `unresolved` and `unresolved_by_severity` (Phase 4B), and the latest run |
| `POST /incidents/correlate` | Run now (idempotent) |
| `PATCH /incidents/{id}` | `{"status": "...", "note": "..."}`: the operator status, which the engine never changes |

Example (abridged):

```bash
curl -s 'localhost:8000/incidents?category=unauthorized_access&severity=critical&page_size=1'
```

```json
{"items": [{"id": 707, "title": "4 denied access attempts with credential CRD-27910 at Gate-C, then door forced open",
  "category": "unauthorized_access", "severity": "critical", "confidence": 0.89, "confidence_level": "high", "status": "open",
  "correlation_state": "active", "event_count": 7, "zones": ["Gate-C"], "devices": ["acs-gate-c", "cctv-gate-c", "soc-console-02"],
  "sources": ["access_control", "cctv", "operator_report"], "conflict_count": 0, "rule_version": "4a.1", "...": "..."}],
 "page": 1, "page_size": 1, "total": 31, "total_pages": 31, "has_next": true}
```

`GET /incidents/707` adds the following:

- `severity_reason`: "Highest severity among strongly linked evidence: critical (evt-…, door forced open); …"
- `confidence_reason`
- `linkage_explanation`: "Linked by the same access credential in a continuous sequence of attempts (3); one record explicitly references the other (1); an alarm on the same door shortly after the attempts (1); same zone and a compatible event type … (1). Contextual items are attached as leaves …"
- `observations`, `inferences`, `missing_information`, `uncertainty` (for example "evt-… is linked by context only …") and `recommended_actions`
- the chronological `timeline`

## Ground-truth evaluation

`backend/datagen/correlation_eval.py` is evaluation tooling only.

- **Positive pairs** are all pairs inside the related events of scenarios expected to be incidents. When the scenario declares groups that must stay separate, only pairs inside each group count. The G reverse-order GPS trails are excluded: they are routine positions delivered late, a data-quality matter rather than a security incident, so no incident is expected.
- **Negative pairs** are pairs across must-remain-separate groups (bystanders, separate vehicles in a cluster, scenario K) and pairs inside scenario L (ambiguous).
- **Correct relationship** = a positive pair in the same incident. **False split** = a positive pair not in the same incident. **False merge** = a negative pair in the same incident, or two events from **different** scenarios in one incident.

### Results (50K mixed, seed 42, generator 1.1.0; clean profile identical except no duplicate or invalid records)

| Measure | Value |
|---|---|
| Incidents | 718 active: 357 traffic, 149 unauthorized access, 100 perimeter, 50 device failure, 49 fleet anomaly, 13 cyber |
| Positive pairs: correct / false splits | 17,608 / 151 of 17,759: **recall 0.992** |
| False merges: labelled negative pairs | **0** of 2,329 (bystanders, separate vehicles in clusters, scenario K, scenario L) |
| False merges: events from different scenarios in one incident | 111 pairs (see below) |
| Pairwise precision | **0.994** |
| Excluding the 12 large auth-burst scenarios (12,514 pairs) | precision 0.979, recall 0.971 |
| Event grouping (size-insensitive) | 592 / 612 expected-incident scenarios fully grouped; on average 99.1 % of a scenario's related events sit in its main incident |
| Background attachments | 13 unlabelled events linked into scenario incidents |

| Scenario | Outcome |
|---|---|
| A multi-sensor traffic | 149 / 150 fully grouped (radar by explicit track reference; phone, seatbelt and ANPR by plate; CCTV by appearance as a leaf; downstream ANPR by plausible movement). The bystander plate read is never merged (281 + 508 negative pairs, 0 merged). |
| B unauthorized access | 85 / 100 fully grouped, 15 mostly grouped. All splits are context that was **deliberately left unattached** because two access incidents at the same gate overlapped in time (loitering detection or report claimable by both). |
| C perimeter intrusion | 47 / 50 fully grouped, 3 mostly grouped (same ambiguity policy) |
| D device failure | 50 / 50 fully grouped (health reports + degraded output) |
| E conflicting sensors | 249 / 250 fully grouped **with the contradiction preserved**. The one miss is a badge/face pair with a second badge in the same 3 s window (ambiguous, so no identity is asserted). |
| F duplicates | Never counted as corroboration (duplicates are not events) |
| G delayed / out-of-order | Grouped by event time; delayed GPS trails, clock skew and store-and-forward uploads raise no incident |
| H malformed | Rejected records never enter incidents |
| I low confidence | No incident raised by low-confidence detections. 14 detections (7 scenarios) attach as "possibly affected output" of a failing device (see below). |
| J repeats / spikes | Auth bursts with success: 12 / 12 cyber incidents. Speeding clusters: one incident per vehicle, **0 of 1,240** cross-vehicle pairs merged. Access-failure spikes and nuisance repeats: no incident (operational data-quality matters). |
| K unrelated | 0 incidents, 0 merges |
| L ambiguous | 0 incidents, 0 forced groupings |

**Where the 111 cross-scenario pairs come from:**

- **99 pairs (14 events from 7 scenario I cases):** low-confidence detections from scenario I that occurred on a device while it was degrading (scenario D). They are attached as "possibly affected output" with the lowest linkage confidence (0.50) and an uncertainty note. The data cannot tell them apart from degradation output.
- **8 pairs:** two scenario B attempt sequences on the same door overlapping in time; the door alarm goes to the most recent sequence.
- **4 pairs:** two separate scenario E GPS faults on the same fleet vehicle within 2 minutes, which form one fleet-anomaly incident.

## Known limitations

- Linkage needs identifiers. Vehicles without a fully read plate, and people without a credential, are linked only by unambiguous context. Ambiguity is resolved by *not* attaching, which costs recall when two similar incidents overlap at one site (most of the scenario B splits).
- The failing device's low-confidence output is attached as "possibly affected", which can include an unrelated weak detection from the same device during the same window. It is marked with the lowest linkage confidence and an uncertainty note.
- Two attempt sequences on the same door that overlap in time can make the door alarm's assignment ambiguous. It goes to the most recent sequence.
- Plate linking cannot detect a cloned plate seen at the *same* site with matching details. Only contradictions (details, travel) are surfaced.
- Thresholds are heuristic and not calibrated on real deployments; confidence scores rank evidence, they are not probabilities.
- Each run recomputes all incidents (about 1–2 s at 50K events). Identity preservation makes this safe, but much larger volumes would need window-scoped incremental correlation.
- Status changes have a minimal API (`PATCH /incidents/{id}`) only. The operator workflow UI is Phase 4B.
