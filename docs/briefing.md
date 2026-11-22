# Operational briefings (Phase 5)

`GET /briefing` produces a shift, daily or custom-period briefing for a security operations manager. It is built only from stored records:

- events;
- Phase 3 quality findings;
- Phase 4 incidents.

Every finding links back to them. The Briefings page (`/briefings`) shows it without any raw JSON.

Code:

| Module | Contents |
|---|---|
| `backend/app/services/briefing/periods.py` | Periods |
| `backend/app/services/briefing/engine.py` | Content |
| `backend/app/api/briefing.py` | API |
| `frontend/src/pages/briefing/` | UI |

Reasoning, confidence and grounding are described in [reasoning.md](reasoning.md).

## Time windows

| Concept | Meaning | Used for |
|---|---|---|
| **Occurrence time** (`occurred_at`) | When the sensor says the event happened | Assigning events to the period: `start <= occurred_at < end` (half-open) |
| Received time (`received_at`) | When the device or gateway sent it | `events_received_after_period_end`: late data a briefing written at the end of the period would have missed |
| Ingestion time (`ingested_at`) | When Zenith stored it | Invalid records only, because they have no trustworthy occurrence time |
| Incident span (`first_occurred_at`–`last_occurred_at`) | First to last linked event | An incident is in the period if its span overlaps it. It is **new** if its first event is inside the period. |
| Incident detection time (`detected_at`) | When the correlation engine first produced it | Shown as "detected after the period ended" when detection came after the period, for example from late data or a replayed dataset |
| Incident status | The operator status **at generation time** | Unresolved means open, acknowledged or investigating. There is no status history, so a past briefing shows current status. |

How periods are resolved:

- All bounds are UTC internally. Shifts and days are defined in a display time zone, an IANA name.
- The time zone is `tz`, defaulting to `ZENITH_BRIEFING_TIMEZONE` (default `UTC`).
- Default shifts are day 06:00–14:00, evening 14:00–22:00 and night 22:00–06:00. Each shift ends where the next starts, and night crosses midnight.
- For example, the Asia/Dubai day 2026-01-15 is `2026-01-14T20:00Z`–`2026-01-15T20:00Z`.
- Days follow the zone's calendar, so a DST change gives a 23-hour or 25-hour day.

Default period (no parameters): the most recent shift that stored data covers for at least half its length, based on the latest stored event. The response explains this choice in `period.default_reason`. A demo dataset from January therefore produces a useful briefing, rather than an empty one for "now".

How events are counted:

- **No double counting.** One `events` row exists per `event_id`, so a repeated submission never adds an event. The briefing reports `duplicate_submissions_ignored` separately.
- **Late and out-of-order events.** They are counted in the period they occurred in, whenever they arrived. A later regeneration includes them.

## Comparisons ("significant" trends)

- **Comparison windows.** For periods up to 24 hours, these are the same hours on the previous days (up to 3). Otherwise they are the preceding equal-length periods.
- **Data coverage.** A window is used only if it lies at least 90% after the first stored event **and** its own stored events span at least 80% of it. The second condition was added in the Phase 7A audit: before it, a data gap, or one stray very old event, made empty days count as zero-activity baselines and produced false "significant increases". Regression test: `test_stray_old_event_does_not_create_empty_baselines`.
- **Incomplete periods.** If the period runs past the latest stored event (by more than 1%), only the covered part is compared, and each comparison window is cut to the same length. A partly empty period is never reported as a drop.
- **Significant increase:** count ≥ 5, Poisson z ≥ 3, and count ≥ 1.5 × the baseline mean.
- **Significant drop:** baseline mean ≥ 5 and count ≤ mean / 3.
- **No baseline:** if no window qualifies, the briefing says that trends cannot be assessed.

All thresholds are `ZENITH_BRIEFING_*` settings.

## Content

| # | Required content | Response field / section |
|---|---|---|
| 1 | Executive overview | `executive_summary` (text, provenance, fallback reason) and `key_metrics` |
| 2 | Highest-priority incidents | `priority_incidents`: unresolved, ranked by the transparent priority score, with factors, reason, strong evidence events and advisory actions |
| 3 | Unresolved incidents | `unresolved_incidents`, `unresolved_total` |
| 4 | Newly detected incidents | `new_incidents`, `new_total` (first event inside the period, plus a `detected_after_period` flag) |
| 5 | Significant incident activity | section `incident_activity`: counts by severity and category, with the most serious incident per category, and a comparison of new incidents against the windows |
| 6 | Repeated events by device | section `repeated_by_device`: Phase 3 repeated-alert and spike findings, devices significantly above their usual volume, and the five busiest devices with their event-type mix |
| 7 | Repeated events by location | section `repeated_by_location`: the same, by zone |
| 8 | Device-health problems | section `device_health`: health reports per device in order (degraded, failure, offline, recovered), whether a recovery was reported, linked device-failure incidents, and devices whose volume dropped sharply. "No events is not confirmation that the device or its area was normal." |
| 9 | Trends and spikes | section `trends`: total against the comparison windows, source types with significant changes, busiest hour, events per hour, spike findings |
| 10 | Data-quality issues | section `data_quality`: counts by type (with share of events for delays, low confidence and unlinkable events), repeated submissions ignored, invalid records not assignable to the period |
| 11 | Conflicts and uncertainty | section `conflicts_uncertainty`: incidents with conflicts (not resolved by the system), conflicting record sets, low-confidence incidents, low-confidence and unlinkable detections |
| 12 | Recommended management actions | `recommended_actions` (rules in [reasoning.md](reasoning.md#recommended-management-actions)) |
| 13 | Overall confidence and limitations | `confidence` (level, score, factors, limitations) |
| 14 | Supporting event references | `evidence_index` (every referenced event), `incident_ids`, `quality_issue_ids`, `grounding` |

Empty periods are explicit. With no events, the summary says: "No events with an occurrence time in … are stored. This is not confirmation that operations were normal: sensors may have been offline or data may not have arrived yet." Each empty section gives an `empty_reason` instead of filler, and confidence is low.

## API

```
GET /briefing?mode=shift|day|custom&date=YYYY-MM-DD&shift=day|evening|night&start=…&end=…&tz=Asia/Dubai&zone=Gate-A&limit=10
GET /briefing/periods          # configured shifts, time zone, stored data range
GET /reasoning/incidents/{id}  # priority breakdown + classified statements for one incident
```

| Parameter | Rules |
|---|---|
| `mode` | `shift` (default), `day` or `custom` |
| `date` | Local date: the day itself, or the day the shift starts. `YYYY-MM-DD`. |
| `shift` | A configured shift name |
| `start`, `end` | Custom periods only. ISO-8601 with an offset; `end` is exclusive and must be after `start`. At most `ZENITH_BRIEFING_MAX_PERIOD_DAYS` (31) days. |
| `tz` | IANA name |
| `zone` | Repeat or comma-separate. Restricts events, incidents and quality findings by their zone facets. |
| `limit` | 1–50 items per list |

Invalid input returns HTTP 422 with `error.details[0].field`. Examples: an unknown time zone, `start`/`end` outside custom mode, a missing or reversed bound, a period that is too long, a timestamp without an offset, an unknown shift, a malformed date.

Example (mixed 50K dataset, seed 42; values copied from an actual response, abridged with "…"):

```bash
curl -s 'localhost:8000/briefing?mode=shift&shift=night&date=2026-01-13'
```

```json
{
  "period": {"mode": "shift", "label": "Night shift, 13 Jan 2026 22:00 – 14 Jan 2026 06:00 (UTC)",
             "start": "2026-01-13T22:00:00Z", "end": "2026-01-14T06:00:00Z", "timezone": "UTC",
             "time_basis": "Events by occurrence time (occurred_at) in [start, end); …",
             "baseline": "2 comparison window(s): the same hours on previous days"},
  "executive_summary": {"text": "Night shift, 13 Jan 2026 22:00 – 14 Jan 2026 06:00 (UTC): 4,338 events from 76 devices in 19 zones. 82 incidents had activity (8 critical, 23 high); 82 are unresolved and 81 started in this period. Needs attention first: #493 Fence cut alarm at Perimeter-North, segment N-09 - person seen by camera (critical, open); …",
                        "generated_by": "deterministic_template", "provider": "deterministic", "validated": true, "fallback_reason": null},
  "key_metrics": {"events_total": 4338, "incidents_active": 82, "incidents_unresolved": 82, "duplicate_submissions_ignored": 70, "…": "…"},
  "priority_incidents": [{"incident_id": 493, "severity": "critical", "status": "open", "priority_score": 78.2, "immediate_attention": true,
                          "reason": "Immediate attention: severity (+40); evidence strength (+14.2); status (+12).",
                          "factors": [{"factor": "severity", "points": 40, "explanation": "Stored severity is critical."}, "…"],
                          "evidence": [{"event_pk": 34338, "event_id": "evt-1c5bb79ccfc9"}, "…"], "…": "…"}],
  "sections": [{"key": "device_health", "findings": [{"basis": "observed",
                 "statement": "anpr-int04: degraded at 14 Jan 01:59 (W-TEMP-HIGH); sensor failure at 14 Jan 02:11 (E-ANPR-IR-ILLUMINATOR); offline at 14 Jan 02:24 (E-HEARTBEAT-LOST); recovered at 14 Jan 04:04. A recovery was reported afterwards.",
                 "quality_issue_ids": [2342, 2492, 2391, 2441], "evidence": [{"event_pk": 32520, "event_id": "evt-604a9cc8f772"}, "…"], "…": "…"}]}, "…"],
  "recommended_actions": [{"basis": "recommendation", "statement": "Acknowledge and assign an owner to 31 open critical/high incidents, starting with #493, #503, #456, #498, #492.", "incident_ids": [493, 503, 456, "…"]}, "…"],
  "confidence": {"level": "medium", "score": 0.69, "factors": [{"factor": "conflicts", "effect": -0.15, "explanation": "24 incident(s) contain conflicting evidence."}, "…"]},
  "grounding": {"events_referenced": 326, "incidents_referenced": 82, "quality_issues_referenced": 60, "all_references_verified": true, "unverified_references": []}
}
```

## Briefings page

| Area | Contents |
|---|---|
| Controls | Shift, Daily or Custom; date; shift; time zone (default, UTC, Asia/Dubai, the browser's zone); zone; Generate. The state lives in the URL, so a briefing can be bookmarked. Custom bounds are entered in the selected time zone (the same zone as shifts and days; fixed in Phase 6, previously the browser's zone) and are sent as UTC ISO-8601. The stored data range is shown. |
| Executive overview | Period (local and UTC), generation time, why a default period was chosen, the summary, and its provenance: "Written by deterministic templates from database values (no language model)", or the model name plus any fallback reason. Key-metric tiles. |
| Highest-priority incidents | Rank, severity, link to the incident, score and reason, "Why this priority" factor table, evidence event chips, first advisory action |
| Lists and sections | Unresolved and new incident lists, the seven sections (the trends section has an events-per-hour bar strip), recommendations, confidence and limitations |
| Evidence references | Grounding status, every event reference, quality findings |

Each finding shows its basis badge (Database fact, Reported by sensors, System inference, Conflict, Missing information, Recommendation), statement, reasoning and confidence, plus any uncertainty or missing information. Its evidence chips work as follows:

- Event chips open the existing event panel: payload, provenance, original raw record and related incidents.
- Quality chips open the quality-issue panel.
- Incident links open the investigation, which shows "Back to briefing".
- "All N events" opens the Events page filtered to exactly the counted events.

Print hides the navigation and controls; the page uses the browser's print function. Loading, error (with the API's validation message) and empty states are explicit.

## Performance

Measured on the laptop described in [benchmarks.md](benchmarks.md#phase-5-briefings-run-of-2026-11-22).

| Dataset | Briefing latency | Notes |
|---|---|---|
| Demo (500 submissions) | about 20 ms median | |
| Clean 50K, 13 periods | median 141 ms, max 427 ms | Full day: about 16,400 events, 275 incidents |
| Mixed 50K, 13 periods | median 152 ms, max 422 ms | Same periods |

The 13 periods are 3 days, 9 shifts and the default. Server peak memory over the whole benchmark, including ingestion, analysis and correlation, was 186 MB. A briefing runs grouped and indexed SQL aggregates plus bounded samples. It never loads raw submissions, and loads only the period's incidents (at most about 275 per day here).

## Tests

`backend/tests/test_briefing.py` (31 tests) covers:

- the structure (every required part) and grounding (every event, incident and quality reference resolves, with matching external ids);
- counts equal to `GET /events` totals;
- half-open shift boundaries (an event at 14:00:00 belongs to the evening shift);
- adjacent windows adding up, and overlapping windows staying consistent;
- the daily briefing; the night shift across midnight; the Asia/Dubai day;
- late, out-of-order and duplicate events (each counted once);
- the empty period; the default period;
- prioritization: explained, deterministic, severity never changed; resolved incidents leave the lists; new versus carried-over incidents;
- the device-health finding and its maintenance recommendation;
- repeated device alerts, with their events and an `event_query` that returns exactly the counted events;
- conflicting evidence reported but not resolved;
- the zone filter; parameter validation; the periods endpoint; incident reasoning;
- briefing reads not modifying events; no duplicate references.

Frontend: `pages/briefing/__tests__/BriefingPage.test.tsx` (11 tests) covers the summary and provenance, basis labels, empty sections, the event, quality, incident and "all events" navigation (including the back link), priority factors, parameters sent, fallback display, the empty period, and loading and errors.

## Known limitations

- Status is the current operator status. There is no status history, so a briefing for a past period cannot show the status at that time.
- Comparison windows need stored history at the same hours. The first day of a dataset has none, and the briefing says so.
- "Significant" uses a Poisson approximation that ignores daily seasonality beyond the same-hours comparison and over-dispersion. It flags candidates for review and is not proof.
- Silent-device detection only covers devices that reported in the comparison windows. A device that never reports cannot be assessed.
- Invalid records are assigned by ingestion time, so they appear in briefings only for periods in which they were received.
- The summary is template text. Optional model wording is limited to that paragraph and validated against identifiers and numbers. Meaning changes using existing tokens cannot be detected automatically.

## Presentation (Phase 7B)

- **Each finding shows by default:** its basis badge, the statement, its confidence, and any uncertainty or missing information. "Why the system says this" expands the rule or statistical reasoning.
- **Long sections:** the first four findings are visible, and the rest are behind "Show N more findings".
- **Printing:** collapsed content is always rendered and always printed (`print:block`), so a printed briefing contains every finding and every evidence reference. Controls are not printed.
- **Incident lists:** detailed cards appear only under *Highest-priority incidents*. *Unresolved* and *Newly started* are compact linked lists with their own counts, and *Significant incident activity* holds category-level statistics.
- **Period banner:** the period name (with local times and zone), why it was chosen, and the generation time. The UTC boundaries, time basis, comparison windows and caveats sit behind "Time basis, UTC boundaries and caveats".
- **Repeated-activity summaries:** the two measures are now named separately. Before, "0 repeated-activity or spike findings; 2 zones significantly above their usual volume" read as a contradiction. The summary now reads, for example, "0 quality-rule spike/repetition finding(s); 2 zones showed elevated activity in statistical comparisons with the same hours on previous days". Counts and algorithms are unchanged.
- **Evidence chips:** IDs longer than 18 characters show a prefix and the distinguishing tail. The full ID is in the tooltip and screen-reader text, and the event panel always shows the full original ID.
