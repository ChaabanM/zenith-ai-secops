import type { DashboardSummary } from "@/types/dashboard";

import { briefing, finding } from "./briefingFixture";

export function dashboard(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  const b = briefing();
  return {
    period: { ...b.period, label: "Evening shift, 14 Jan 2026 14:00 – 14 Jan 2026 22:00 (Asia/Dubai)", start: "2026-01-14T10:00:00Z",
      end: "2026-01-14T18:00:00Z", timezone: "Asia/Dubai", default_reason: "No shift given: the most recent shift at least half covered by stored data.",
      query: { mode: "shift", date: "2026-01-14", shift: "evening", tz: "Asia/Dubai" } },
    generated_at: "2026-11-22T12:00:00Z",
    filters: { zones: [] },
    scope: { period: "Period figures cover the evening shift.", status: "Incident status is the operator status now.",
      stored_totals: "'All stored data' figures ignore the period and the zone filter.", scores: "Heuristic scores, not probabilities." },
    period_metrics: { events_total: 4625, devices_reporting: 76, zones_reporting: 19, incidents_active: 70, incidents_unresolved: 68, incidents_new: 66,
      incidents_high_critical: 41, incidents_unresolved_high_critical: 40, incidents_by_severity: { critical: 8, high: 33 }, quality_issues_total: 539,
      duplicate_submissions_ignored: 77, devices_with_health_reports: 9, devices_without_reported_recovery: 1, devices_with_volume_drop: 1,
      device_health_concerns: 2, latest_stored_event_at: "2026-01-14T23:59:52Z", period_extends_past_stored_data: false },
    stored_totals: { events_stored: 47500, submissions_invalid: 1500, submissions_duplicate: 1000, incidents_active: 718, incidents_unresolved: 716,
      incidents_unresolved_high_critical: 370, quality_issues: 6999, data_start: "2026-01-12T00:00:30Z", data_end: "2026-01-14T23:59:52Z" },
    priority_incidents: b.priority_incidents,
    events_by_source: [{ value: "cctv", count: 3000 }, { value: "anpr", count: 1625 }],
    events_per_hour: [{ hour_utc: "2026-01-14 10", count: 500 }, { hour_utc: "2026-01-14 11", count: 700 }],
    spikes: [finding({ id: "trends-3", category: "trends", basis: "inference", statement: "Spike at device acs-gate-c: 17 events in 5.0 min.",
      quality_issue_ids: [44], incident_ids: [], uncertainty: [] })],
    quality_by_type: [{ value: "delayed_event", count: 145 }, { value: "low_confidence_detection", count: 116 }],
    quality_high_severity: [{ id: 31, issue_type: "device_offline", severity: "high", title: "radar-int05 offline", occurred_from: "2026-01-14T12:00:00Z",
      occurred_to: "2026-01-14T12:00:00Z", event_count: 1 }],
    quality_high_severity_total: 31,
    uncertainty: { low_confidence_detections: 116, unlinkable_events: 99, conflicting_record_sets: 37, incidents_with_conflicts: 25, delayed_events: 145 },
    device_health: [finding()],
    device_health_note: "Devices that send no health reports cannot be assessed, and no reports does not mean a device is healthy.",
    briefing: { executive_summary: b.executive_summary, recommendations: b.recommended_actions, recommendations_total: 6, confidence: b.confidence },
    grounding: { events_referenced: 273, incidents_referenced: 70, quality_issues_referenced: 82, all_references_verified: true, unverified_references: [] },
    ...overrides,
  };
}
