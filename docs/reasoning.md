# Reasoning (Phase 5)

The reasoning layer turns stored records into statements a manager can act on. Each statement:

- says what kind of claim it is;
- carries the events, incidents and quality findings it rests on;
- says how confident the system is.

It reuses the Phase 3 quality findings and the Phase 4 incidents, and changes neither of them.

Code: `backend/app/services/reasoning/`. The briefing that uses it is in `backend/app/services/briefing/` (see [briefing.md](briefing.md)).

## What kind of "AI" this is

The requirements ask for AI "where it adds value", with a deterministic fallback. This implementation is honest about which technique does what.

| Technique | Where | What it does |
|---|---|---|
| **Rules** (deterministic) | Phase 3 quality rules, Phase 4 correlation, recommendation rules (below) | Detect, link and recommend from explicit conditions |
| **Heuristic scoring** (deterministic) | Incident confidence (Phase 4), incident priority, briefing confidence | Weighted sums with documented weights. These are rankings, **not calibrated probabilities**. |
| **Statistical inference** (deterministic) | "Significantly above or below the usual level" in briefings; Phase 3 spike rule | A Poisson comparison of a count against comparison windows. Labelled `inference`, with its z-score. |
| **Language generation (templates)** | Every briefing sentence and the executive summary | Fixed sentence templates filled with database values. No model is involved. |
| **Language generation (optional local model)** | Executive summary wording only | If `ZENITH_REASONING_PROVIDER=ollama`, a local model served by [Ollama](https://ollama.com) may reword the template summary. Its output is accepted only if it passes the grounding check, otherwise the template is used. |

**No language model was used to produce any result in this repository's tests, benchmarks or verification.** The default provider is `deterministic`. Ollama was not installed on the development machine, so the Ollama adapter is tested only against stub providers and an unreachable server, not against a real model. The UI and API always say which text was shown: `executive_summary.generated_by` is `deterministic_template` or `local_language_model`.

### Why model output can only reword

The model receives the deterministic summary and a JSON fact list. Its answer is checked by `grounding.check_narrative`. The answer is rejected, and the template text is used instead, if it:

- is empty or longer than `ZENITH_REASONING_MAX_NARRATIVE_CHARS` (2,000);
- contains an event id (`evt-…`) that is not in the grounded content;
- contains an incident or finding number (`#123`) that is not in the grounded content;
- contains any hyphenated identifier (a device such as `radar-int05`, or a zone such as `Gate-A`) that is not in the grounded content, apart from a short list of ordinary English words such as "low-confidence";
- contains any number that does not appear in the grounded content.

Provider errors and timeouts (`ZENITH_REASONING_TIMEOUT_SECONDS`) also fall back. The reason is returned in `executive_summary.fallback_reason` and shown in the UI. The model never touches the structured findings, priorities, references or counts. It can only change the wording of one paragraph.

The check catches new identifiers and numbers. It cannot catch a model that changes meaning with existing tokens, for example by dropping a "not" or calling an inference a fact. That is why model wording is optional, labelled, and limited to the summary paragraph.

## Structured findings

`schemas/briefing.py → Finding`:

| Field | Meaning |
|---|---|
| `basis` | `database_fact` (exact count or aggregate), `observed` (what sensors or operators reported, not verified), `inference` (drawn by rules, heuristics or statistics), `conflict` (contradictory records), `missing` (an evidence gap), `recommendation` (advisory) |
| `statement`, `reasoning` | What is claimed, and why |
| `confidence` | `level` (high, medium or low), optional heuristic `score`, and its `basis` (for example "exact database count", "device / monitor reports", "statistical comparison", "incident confidence (heuristic)") |
| `uncertainty`, `missing_information` | Explicit caveats |
| `evidence` | Sample of supporting events as `{event_pk, event_id}`. The original external id is preserved exactly. |
| `evidence_total` | All supporting events, which can exceed the sample |
| `event_query` | `GET /events` filters that list every supporting event, for example device and period |
| `incident_ids`, `quality_issue_ids` | Related records |
| `recommended_actions`, `metrics` | Actions and the numbers behind the statement |

Sensor reports are never turned into facts. Device-health statements, for example, are `observed`: "as reported by the monitoring system… not independent verification". Low-confidence detections are described as "sensor reports, not verified identifications".

## Grounding guarantees

1. Every count is a SQL aggregate over stored rows, never an estimate.
2. Every finding's references come from the same query that produced it.
3. `grounding.verify_references` re-reads every referenced event, checking that the id exists **and** that its `event_id` matches, plus every incident and quality issue, before the briefing is returned. The result is in `briefing.grounding`. `all_references_verified` was true for every briefing in the tests and in both 50K benchmarks (26 periods; 10,748 event references, 2,950 incident references and 2,092 quality references re-checked against SQL by the benchmark).
4. Production code never reads generator ground truth. `test_production_code_never_reads_ground_truth` scans all of `app/`, including these services.

## Incident prioritization

`reasoning/prioritization.py`. The score is a sum of named factors, each returned with its points and a sentence. It **never changes stored severity**.

| Factor | Points |
|---|---|
| Severity (stored) | critical 40, high 28, medium 15, low 6, informational 2 |
| Status | open 12 (not acknowledged), acknowledged 6, investigating 4. Resolved and false-positive incidents score 0 and are never prioritized. |
| Recency of the latest event before the reference time (the period end) | ≤ 1 h: 10; ≤ 4 h: 6; ≤ 12 h: 3; otherwise 1 |
| Evidence strength | 15 × heuristic incident confidence |
| Corroboration | 2 per additional independent **source type**, up to 3. Repeated submissions are not events, and ten events from one source type add nothing. |
| Unresolved conflicts | 4 per conflict, up to 2. A contradiction must be resolved before acting. |
| Device failure | 5 (loss of sensor coverage) |
| Anomalies | 3 if any of the incident's events are in a spike or repeated-alert finding |

An incident is marked **immediate attention** if it is unresolved and either critical or high, or scores at least 55. Ties are broken by severity, then most recent activity, then lowest incident id. All weights are in `ReasoningSettings` and can be overridden with `ZENITH_REASONING_*`.

## Overall briefing confidence

This is a heuristic data-reliability score that starts at 1.0. Each deduction is listed in the response, under `confidence.factors`:

| Factor | Deduction |
|---|---|
| No events in the period | 0.6 (the level becomes low) |
| Low-confidence detections | share of events, at most 0.15 |
| Delayed events | 2 × share, at most 0.1 |
| Repeated submissions | share, at most 0.05 |
| Events received after the period ended | 0.05 |
| Devices with a fault and no recovery | 0.05 each, at most 0.15 |
| Incidents containing conflicts | 0.03 each, at most 0.15 |
| Invalid records | 0.01 each, at most 0.1 |
| Period extends past the latest stored event | 0.1 |

The level is high at 0.8 or above, medium at 0.6 or above, and low otherwise. Fixed limitations are always listed: counts can change with late data, status is current rather than historical, and scores are not probabilities.

## Recommended management actions

These are rules over the findings. Each recommendation is a `Finding` with `basis = recommendation` and the ids that triggered it.

| Condition | Recommendation |
|---|---|
| Open (unacknowledged) critical or high incidents | Acknowledge and assign owners, listing the incidents |
| Unresolved incidents with conflicts | Resolve the conflicting evidence before enforcement or follow-up |
| Device-health problem with no recovery in the period | Dispatch maintenance to the listed devices |
| A device's volume dropped sharply against its usual level | Confirm the devices are operating |
| Delayed events | Investigate transmission delays, naming the busiest devices |
| Low-confidence detections | Check calibration and camera condition, naming the busiest devices |
| Repeated device alerts | Review repeated alerts |
| Invalid records | Ask integration owners to fix malformed records |
| None of the above | "No management action is indicated by the stored data for this period." |

Nothing is ever executed.

## `GET /reasoning/incidents/{id}`

Returns the incident's priority breakdown, with recency measured against the latest stored event, and its stored Phase 4 statements classified by basis:

- observations become `observed`;
- inferences and uncertainty become `inference`;
- conflicts become `conflict`;
- missing information becomes `missing`;
- recommended actions become `recommendation`.

Each statement carries the incident's evidence. Strongly linked events support observations and inferences, conflicting links support conflicts, and contextual links support uncertainty.

## Tests

| File | Covers |
|---|---|
| `tests/test_reasoning.py` (21 tests) | The narrative check: valid text accepted; unknown event id, incident number, device, number, empty and too-long text rejected. Default deterministic provider. Grounded model output used and labelled. Fabricated, numeric and failing or timed-out providers fall back with a reason. Unreachable Ollama falls back. End-to-end briefing with a fabricating provider, where the structured findings are unchanged. Reference verification detects fabricated and mismatched ids. Shift and day periods, the default shift, a DST day, comparison windows. Priority factors, tie-breaking, resolved incidents, source-type corroboration, the conflict cap. |
| `tests/test_briefing.py` (31 tests) | See [briefing.md](briefing.md#tests) |
