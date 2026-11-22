# Operations overview (Phase 6)

The Dashboard (`/`) is the landing page. It answers, for one clearly labelled reporting period:

- What happened?
- What needs attention?
- Which incidents are unresolved?
- Are devices reporting problems?
- How reliable is the data?

Every figure comes from `GET /dashboard/summary`. That endpoint is a compact projection of the Phase 5 briefing engine, so the dashboard and the briefing for the same period always show the same numbers.

## Reporting period

- **Default:** the most recent shift that stored data covers for at least half its length. "Latest day with data" is the alternative.
- **Rules:** the same as the briefing (see [briefing.md](briefing.md#time-windows)).
- **Visibility:** the period appears as a banner at the top, with local times, a time-zone label, the time basis and why the period was chosen.

| Scope | Shown as | Basis |
|---|---|---|
| Period figures | The six metric cards and every panel | Events by **occurrence time** in `[start, end)`; incidents whose activity **overlaps** the period; quality findings whose span overlaps it |
| Incident status | "Unresolved", "status now" | The operator status **now**, not at the end of the period |
| All stored data | One line under the cards, labelled "All stored data (ignores period and zone)" | All rows, regardless of period and zone |
| Scores | Incident confidence, priority, briefing confidence | Heuristic; "not probabilities" is stated on the page |

Controls: period (latest shift or latest day), time zone (server default, UTC, Asia/Dubai, or this browser's zone), and zone. The state lives in the URL. Every time on the page carries a time-zone label, for example "GMT+4" or "UTC". Hour buckets are shown in the selected zone.

## Panels

| Panel | Contents | Kind of information |
|---|---|---|
| A. Operational summary | Six cards: events (devices and zones), incidents with activity (started in period), unresolved incidents, high/critical incidents (unresolved), data-quality findings (high/critical), device-health concerns (devices with health reports). Each card opens the matching filtered list for the same period, and that list shows the same count. | Database facts |
| B. Priority incidents | Top 5 unresolved incidents by the transparent priority score: severity, status, link, title, category, zones, last event time, heuristic confidence, priority score and reason, conflicts, evidence event chips | Inferred relationships (correlation) plus operator status |
| C. Event activity | Events by source (each bar opens the Events page filtered by source and period), events per hour, and spikes reported by the data-quality engine (open the finding) | Sensor-reported events (counted from the database); spikes are system inferences |
| D. Data quality | Leading finding types (open the filtered quality list), high/critical findings (open in place), and reliability indicators: low-confidence detections, unlinkable events, delayed events, conflicting record sets, incidents with conflicts, repeated submissions | Quality warnings |
| Device health | Health reports per device, in order (observed), and devices whose volume dropped sharply against the same hours on previous days (missing information) | Sensor/monitor reports; an absence of data is shown as missing information |
| E. Briefing preview | The briefing's executive summary (with how it was written: templates, no language model), briefing confidence, the top 3 advisory recommendations with their evidence, and "Open full briefing" for exactly the same period (it carries the resolved `mode`, `date`, `shift`, `tz` and zone) | Recommendations |
| Evidence and grounding | "All N events, M incidents and K quality findings behind this overview were verified against the database", or the list of unverified references | |

Honest presentation:

- A device is never shown as "healthy" or "online". The page states that health comes only from health reports and volume drops, that devices without health monitoring cannot be assessed, and that no reports does not mean a device is healthy.
- Empty panels say what was not found and that this is not confirmation of normal operations.

## Navigation

| Path | How |
|---|---|
| Dashboard → incident → evidence | Priority incident link → investigation → timeline event → event panel with the original raw record. "Back to overview" returns to the dashboard. |
| Dashboard → quality issue → source event | High/critical finding or spike → quality panel → "Open event" → Events page with the event panel open |
| Dashboard → briefing | "Open full briefing", for the same period and zone |
| Dashboard → filtered lists | Every metric card, source bar and quality-type bar |
| Briefing → incident → evidence | Incident links in the briefing → investigation ("Back to briefing") |
| Event → related incident | "Related incidents" in every event panel |
| Quality issue → event | "Open event" / "View raw" in every quality panel |

## Empty database and demo data

With no stored events, the dashboard shows "No events stored yet", explains that this is not a sign that operations are normal, and offers two routes:

- **Events → Import events** (`/events?import=1` opens the import panel), then choose `data/samples/demo_events.json`;
- `python3 scripts/load_demo.py` (standard library only).

`scripts/load_demo.py` posts the committed demo dataset to `POST /ingest`, waits for the background quality analysis and correlation, and prints the counts. It **refuses to run if the database already contains events**, so demo data is never mixed in silently; `--force` overrides this. Nothing is ever loaded automatically.

## API

```
GET /dashboard/summary?mode=shift|day|custom&date=&shift=&start=&end=&tz=&zone=
```

The parameters and validation are identical to `GET /briefing`.

| Response field | Contents |
|---|---|
| `period` | Includes `query`, the parameters that reproduce exactly this period in `GET /briefing` |
| `scope` | Text explaining each scope |
| `period_metrics` | Period figures |
| `stored_totals` | All stored data |
| `priority_incidents` | Top 5 |
| `events_by_source`, `events_per_hour` | Event activity |
| `spikes` | Spike findings |
| `quality_by_type`, `quality_high_severity` (top 5), `quality_high_severity_total` | Data quality |
| `uncertainty` | Reliability indicators |
| `device_health`, `device_health_note` | Device health |
| `briefing` | Executive summary, top 3 recommendations, confidence |
| `grounding` | Every event, incident and quality reference re-verified, including the extra high-severity findings |

Code: `backend/app/api/dashboard.py`, `backend/app/services/briefing/dashboard.py`, `frontend/src/pages/dashboard/DashboardPage.tsx`.

## Tests

| File | Covers |
|---|---|
| `backend/tests/test_dashboard.py` (12 tests) | Period metrics equal the briefing and the events API; source and hour sums equal the event total; priority order equals the briefing; stored totals are all-time and labelled; unresolved counts follow operator status; quality and device health are grounded (no "healthy" claims); zone and time zone; default period and reproducible query; empty database; validation; read-only; and **every linked list reproduces the dashboard's count** (incidents, unresolved, high/critical, quality, high/critical quality, events, per source) |
| `frontend/src/pages/dashboard/__tests__/DashboardPage.test.tsx` (13 tests) | Period, time-zone label and metrics; labelled all-time totals; metric links; priority order and investigation with back link; quality issue to event; evidence events; briefing preview link; activity hours in the period's zone; device health never shown as healthy; controls sent to the API; empty database to the import panel; loading, error and retry; unverified references flagged |
| `frontend/src/pages/events/__tests__/format.test.ts` (4 tests) | Zone-aware input conversion (Asia/Dubai, UTC, BST, DST change); round trip; time-zone labels; hour labels |

## Performance

| Dataset | Default (shift) | Latest day |
|---|---|---|
| Demo | 24 ms | 26 ms |
| Clean 50K | 140 ms | 440 ms |
| Mixed 50K | 166 ms | 444 ms |

Each figure is the median of 5. Counts and grounding were checked against SQL in the benchmark. The day view costs the same as a full-day briefing, because it is one.

## Known limitations

- The period selector offers the latest shift or the latest day. Other shifts, days and custom ranges are available on the Briefings page, which the dashboard links to.
- "Device-health concerns" counts devices whose health problem has no reported recovery in the period, plus devices whose volume dropped sharply. A volume drop on a small dataset (for example the 500-record demo) can rest on a baseline of about five events, so treat it as a prompt to check, not as an outage.
- Status is current, not historical.
- The dashboard does not poll. Use Refresh after new data arrives.

## Presentation (Phase 7B)

The period banner shows the period name (with local times and the zone), why it was chosen, and any incomplete-data warning. Local and UTC boundaries, what is counted, and the status and late-arrival caveats sit behind "Time basis, UTC boundaries and caveats". That section is collapsed on screen and always printed.
