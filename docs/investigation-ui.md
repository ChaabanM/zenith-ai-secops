# Incident investigation dashboard (Phase 4B)

The Incidents page and the incident investigation view let a security operations manager answer, without reading raw JSON:

- What is happening?
- Which events are related, and why?
- How serious is it?
- How reliable is the evidence?
- What conflicts or is missing?
- What should happen next?

Everything on these screens comes from the backend: `GET /incidents`, `GET /incidents/summary`, `GET /incidents/{id}`, `GET /events/{id}`, `GET /quality-issues/{id}` and `PATCH /incidents/{id}`. The frontend computes no correlations, severities, confidences or explanations. Its only derivations are display ones: counting timeline entries per evidence class, ordering the timeline, and grouping conflict records into columns.

## Routes and components

| Route | Component | Purpose |
|---|---|---|
| `/incidents` | `pages/incidents/IncidentsPage.tsx` | Summary cards, server-side filters, sortable and paginated incident table |
| `/incidents/:id` | `pages/incidents/IncidentDetailPage.tsx` | Investigation view |
| `/incidents/:id?event=<pk>` | `EventDetailPanel` (reused from the Events page) | Source event, source-specific payload, provenance, original raw submission, related incidents |
| `/incidents/:id?issue=<id>` | `QualityIssueDetailPanel` (reused from Data Quality) | The quality finding with its supporting events and raw records |

Supporting modules:

| Module | Contents |
|---|---|
| `IncidentTimeline.tsx` | Chronological timeline with evidence-class filters |
| `ConflictComparison.tsx` | Contradictory records side by side |
| `StatusControls.tsx` | Operator actions and the confirmation dialog |
| `badges.tsx` | Status, confidence and evidence-class badges |
| `labels.ts` | Display names only |

The API client is in `services/api.ts` and the types are in `types/incidents.ts`.

## Incident overview

**Summary cards** come from `GET /incidents/summary`:

| Card | Main count | Secondary count |
|---|---|---|
| Total | all active incidents | unresolved |
| New / open | open | acknowledged |
| Critical | all critical | unresolved critical |
| High severity | all high | unresolved high |
| Investigating | investigating | — |
| Resolved | resolved | false positive |

"Unresolved" means open, acknowledged or investigating. Clicking a card applies the matching filter.

**Table columns:** ID, title (with category and conflict count), severity, confidence, status, zones, first observed, last observed, number of linked events, and last content update.

**Priority:** the default sort is `priority`, a backend sort.

- Unresolved incidents come first, then the most severe, then the most recent. Ties are broken by id, so pages are stable.
- Unresolved critical rows have a red edge and unresolved high rows have an orange edge.
- Resolved and false-positive rows are muted.
- Backend severity values are never changed.

Other sorts are most severe, most recent activity, oldest first, and highest confidence.

**Filters** are all server-side and stored in the URL:

- Severity (including "critical + high")
- Status (including "unresolved")
- Category
- Source type
- Zone
- Device ID
- Time range: "Active from" means the incident's last event is at or after this time. "Started before" means its first event is before this time.

The page also has sort, page size (25/50/100), Previous/Next, the total result count, and clear filters. Only one page of incidents is fetched.

**Returning from an investigation:** opening an incident passes the list's query string in the navigation state. "Back to incidents" returns to the same filters, sort and page. This still works after a browser refresh.

**States:**

- Loading.
- Empty: either "no incidents yet" with how to create them, or "no incidents match these filters" with a clear action.
- Error, with retry. A summary failure is reported separately from a list failure.

**Run correlation** calls `POST /incidents/correlate`. That call is idempotent and never changes operator status.

## Investigation view

| Section | Contents (backend field) |
|---|---|
| Header | Incident ID, category, rule version, title, severity, status, confidence (score and level, marked heuristic), evidence summary text, first and last observed, first detected and content last updated, zones, devices. Merged or obsolete incidents show a banner linking to the surviving incident. |
| Evidence summary | Linked source events (`timeline_total`), strongly linked (`details.core_events`), contextual (`details.contextual_events`), conflicts, uncertain timeline entries, missing-information notes, source categories, and repeated submissions ignored as corroboration |
| Why these events are grouped | `linkage_explanation`, plus a table of the link types present: basis, meaning (`link_description`), strong or contextual, and count. Time-and-place-only links are named that way, for example "Time and place pairing only (no shared identifier)". |
| Timeline | See below |
| Evidence reliability | Observed or reported (`observations`), system inferences (`inferences`), conflicting evidence (`conflicts` plus the side-by-side comparison), missing information (`missing_information`), uncertainty (`uncertainty` plus a count of contextual and uncertain entries) |
| Investigation status | Status, operator note, when it was set, and the action buttons |
| Recommended next actions | `recommended_actions`, headed "Recommendations only. Nothing below has been performed or triggered." This is kept separate from the status record, which is the only action an operator takes here. |
| Severity and confidence | `severity_reason` and `confidence_reason`, with "heuristic rankings, not calibrated probabilities" |
| Related data-quality findings | `quality_issues`. Each finding opens in place, with a link to the Data Quality page. Six are shown before a "show all" control. |
| Merged incidents | `merged_incident_ids` |

When the backend reports nothing for a section, it says so: for example "No conflicts were reported by the correlation engine." Nothing is generated to fill the gap.

## Timeline

Entries are ordered by `occurred_at`, with ties broken by internal event id. The frontend re-sorts by these keys and never uses arrival order. Entries that arrived late show "Received N min after it occurred".

Each entry shows:

- Occurred time.
- Evidence class.
- Event severity.
- Original `event_id`, which opens the source event.
- Source type, event type, device and zone.
- Role.
- Link basis.
- Link strength, described as heuristic.
- Sensor-reported confidence, or "not reported". Values below the engine's threshold show "(below 0.60)".
- The engine's linkage explanation and link-type meaning.
- Any conflict.
- Chips for quality findings on that event.
- Submission ID.

### Evidence classes

Each class has a coloured rail and badge, and can be toggled on and off. The class comes only from backend fields, in this order of precedence:

| Class | Rule | Meaning |
|---|---|---|
| Conflicting (red) | `conflict` is set | The record contradicts other evidence. Neither side is treated as correct. |
| Uncertain (amber) | `low_source_confidence` is true, or `link_type = affected_output` | Low sensor confidence, or output of a failing device during its degradation. Labelled "not verified identity evidence". |
| Strong link (dark) | `core` is true | A shared identifier or an explicit reference |
| Contextual (dashed) | otherwise | Time, place and event type only. It may be unrelated. |

A low-confidence detection therefore never appears as plain strong evidence, even when the engine linked it as core. Phase 4A's weak cross-scenario attachments (for example, low-confidence face captures during a camera degradation) show as Uncertain.

Rendering is bounded:

- The backend returns at most 2,000 entries per incident and reports `timeline_total`.
- The UI renders 100 at a time, with a "show more" control.
- The largest incident in the 50K dataset has 61 events.

## Original evidence traceability

| Path | How |
|---|---|
| Incident → source event → raw submission | Select an event ID on the timeline. The event panel shows the normalized event, source-specific payload, device, original timestamps, received time and delay, validation notes, provenance (submission ID, batch, record position, SHA-256), the raw record exactly as submitted, and any repeated submissions of the same event ID. |
| Incident → quality issue → supporting event | Select a finding in "Related data-quality findings", a timeline chip, or a conflict group. The issue panel lists its evidence events, with "Open event" links and "View raw" for each submission. |
| Event → incident | The event panel, on both the Events page and the incident view, has a "Related incidents" section from `GET /incidents?event=<id>`. Inside an incident, the current incident is marked "(this incident)". |

Evidence is fetched when a panel opens and is not copied into incident state. If a record is unavailable, the panel shows the backend error.

## Conflicts side by side

For each conflict-type quality finding (conflicting sensor results, timestamp conflict, location conflict) that references two or more of the incident's events, those records are shown as columns: source, device, event type, occurred time, zone and sensor confidence. A timeline entry the engine marked with a conflict, but which has no such finding, is shown beside the incident's anchor event.

"Compare reported values" loads each event's payload and adds one row per payload field. Rows where the records differ are highlighted. The text reads: "The system does not decide which record is correct."

## Confidence interpretation

| Quantity | Display | Meaning |
|---|---|---|
| Incident confidence | `0.89 high (heuristic)`, never a percentage | Heuristic ranking from linkage strength, detection confidence, corroboration and conflicts (`confidence_reason`). Not a probability. |
| Link strength | `Link strength (heuristic): 0.90` | Fixed per link type, reduced when the link carries a conflict |
| Sensor-reported confidence | `Sensor-reported confidence: 0.42 (below 0.60)` | The device's own value, not verified by Zenith |

## Operator status

The available actions are Acknowledge, Start investigating, Resolve, Mark false positive and Reopen. These are exactly the statuses `PATCH /incidents/{id}` accepts. The current status is not offered.

1. Choosing an action opens a confirmation dialog. It shows the incident ID, the current and proposed status, and an optional note (up to 2,000 characters; it replaces the previous note). The dialog has Cancel, Escape and click-outside to cancel, and it focuses Cancel by default.
2. Nothing is sent until the operator confirms.
3. While saving, the buttons are disabled and a spinner is shown.
4. On failure, the dialog stays open and shows "Not saved: <backend message>". The status shown on the page does not change.
5. On success, the page shows the status and note returned by the backend, with "Status saved".

Status is stored in operator-only columns that correlation never writes. It survives refreshes and re-correlation; this was verified in the browser. No status triggers any other action, and there is no authentication or role management in this phase.

## Layout

- Desktop: a two-column investigation view, with evidence on the left and status, recommendations and reasons on the right.
- Below the `md` breakpoint: the sidebar becomes a menu button and the columns stack. At 390 px there is no horizontal page scroll; wide tables scroll inside their own container.
- Severity colours match the Events and Data Quality pages.

## Backend additions (Phase 4B)

These were added only where the UI needed them. Each has regression tests in `backend/tests/test_incidents_api.py`.

| Change | Why | Tests |
|---|---|---|
| `sort=priority` on `GET /incidents` | Unresolved first, then severity, then recency; stable across pages | `test_priority_sort_puts_unresolved_first_then_severity` |
| `unresolved` and `unresolved_by_severity` on `GET /incidents/summary` | Counts of unresolved critical and high incidents | `test_summary_unresolved_counts_follow_status` |
| `link_description` and `low_source_confidence` on each timeline entry, and `low_source_confidence_threshold` on the detail | The UI marks uncertain evidence using the engine's own threshold (`ZENITH_CORRELATION_LOW_SOURCE_CONFIDENCE`) instead of a hard-coded copy | `test_timeline_flags_low_source_confidence_and_describes_links` |
| none (existing `event` filter) | Event → incident navigation | `test_event_filter_links_event_back_to_its_incident` |
| none (existing behaviour, now tested) | Note persistence, 404, rejected update changes nothing | `test_status_note_persists_and_unknown_incident_patch_is_404` |

Correlation thresholds, clustering rules and incident content are unchanged (rule version `4a.1`).

## Tests

Frontend component tests use Vitest, Testing Library and jsdom (dev dependencies only). They run with `cd frontend && npm test`, and `./evaluate.sh` runs them too. They use a routed fetch mock that records every request, so each test asserts both what is rendered and what was sent to the backend.

| File | Covers |
|---|---|
| `pages/incidents/__tests__/IncidentsPage.test.tsx` (15 tests) | Summary counts; table fields; priority sort and pagination; severity, status, category, source and zone filters; device and time filters and clearing; filters restored from the URL; navigation state; summary-card filtering; loading, empty, filtered-empty and error-with-retry states |
| `pages/incidents/__tests__/IncidentDetailPage.test.tsx` (12 tests) | Section 4.2 fields; timeline order and tie-breaking; evidence classes (conflicting, uncertain, strong); original event IDs and linkage explanations; event → raw submission → related incident; quality issue → supporting-event link; facts, inferences, side-by-side conflicts, missing information and uncertainty; truthful empty states; advisory recommendations; not-found; back-link context; load error with retry |
| `pages/incidents/__tests__/StatusControls.test.tsx` (4 tests) | Confirmation content with nothing sent first; cancel sends nothing; confirmed status and note sent, success shown, persisted after a fresh render; backend failure shows no success and keeps the status |
| `pages/events/__tests__/EventDetailPanel.test.tsx` (2 tests) | Event → related incident link; truthful message when an event is in no incident |

## Verification

Real-browser verification used headless Google Chrome, driven by `puppeteer-core` from a scratch directory, not a project dependency. The run used:

- a fresh SQLite database;
- the committed demo dataset (500 submissions), ingested through `POST /ingest`;
- the background quality analysis and correlation, which produced 92 quality issues and 16 incidents.

All 20 checks passed:

1. The list loads.
2. Every summary card and the result count equal the API values.
3. The severity filter and severity + category filter are server-side and match the API.
4. The investigation opens, and "Back" restores the filtered list URL.
5. The timeline is ordered by `occurred_at` and contains exactly the API's event IDs.
6. Selecting an event shows that event.
7. The raw record shown is identical to `GET /submissions/{id}`, and the event panel links back to "(this incident)".
8. A quality finding opens with supporting-event links.
9. Every backend conflict and uncertainty note is visible.
10. Recommended actions equal the API list.
11. The confirmation dialog shows the ID, current and new status, and nothing has been sent.
12. A confirmed change and note are stored in the backend.
13. Cancel changes nothing.
14. Status and note survive a page refresh and a `POST /incidents/correlate` followed by another refresh.
15. At 390 px there is no horizontal overflow on the list or the detail.

There were no uncaught page errors.

Against the 50K mixed benchmark database (718 incidents), from the same laptop:

| Measure | Result (median of 5) |
|---|---|
| `GET /incidents?sort=priority` (page 1 / last page) | 7.9 / 7.4 ms |
| `severity=critical,high` + `status=unresolved` | 6.7 ms |
| Source + zone / device / 8-hour window | 2.5 / 4.2 / 5.7 ms |
| `GET /incidents/summary` | 4.8 ms |
| Largest incident detail (61 events) | 5.8 ms |
| `GET /incidents?event=<id>` | 2.6 ms |
| Browser, full page load to rendered rows (Vite dev server, unbundled) | list 134 ms; 61-event investigation 138 ms |
| Pagination | page 2 loads in the UI; walking every API page returns 718 unique incidents |

## Known limitations

- The status note is a single field. Each change replaces it, and there is no status history or audit trail. An operator identity is not recorded because there is no authentication in this phase.
- The incident list is refreshed manually, with Refresh or Run correlation. It does not poll while a background correlation is running.
- Zone and device suggestions come from event facets (all events), not only from zones and devices that appear in incidents.
- Conflict columns are formed from conflict-type quality findings and engine conflict markers. A contradiction that neither reports is not shown side by side.
- Some backend uncertainty texts repeat for each quality finding on the same event. They are shown as returned.
- Docker was verified manually on macOS after this phase; see the README's *Docker verification status* for what was and was not checked.

## Readability changes (Phase 7B)

- **Up front:** title, severity, status, heuristic confidence, location (zones), the summary, first and last observed times, and devices.
- **Collapsed:** "Record details" holds detection time, last update and rule version. "How each link was established" holds the link-type table. Observations and inferences show their first three items, followed by "Show N more".
- **Printing:** collapsed content stays in the page and always prints (`print:block`).
- **Never collapsed:** conflicts, missing information and uncertainty.
- **Conflicts:** shown in a warning box with a count, the contradictions and the side-by-side comparison when the engine reports any. Otherwise a neutral note: "No conflicts reported by the correlation engine. This does not prove that every observation is correct."
- **Contextual versus uncertain:** the evidence summary explains both kinds.
  - Contextual evidence is attached because of supporting circumstances (zone, time window, compatible event type) rather than a strong identifier. It is never identity evidence and may be unrelated.
  - Uncertain entries are records flagged for sensor-reported confidence below the engine threshold, or for output from a device during reported degradation.
  - The counts are unchanged; a contextual item counts as uncertain only if it is also flagged.
