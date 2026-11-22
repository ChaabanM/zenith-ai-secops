import type { Briefing, Finding } from "@/types/briefing";

export function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: "device_health-1", category: "device_health", basis: "observed",
    statement: "radar-int05: offline at 15 Jan 12:46. No recovery was reported in this period.",
    reasoning: "Health status as reported by the monitoring system.", confidence: { level: "high", score: null, basis: "device / monitor reports" },
    uncertainty: ["Whether the device is working now is unknown from this period's data."], missing_information: [],
    evidence: [{ event_pk: 7, event_id: "h1" }], evidence_total: 1, event_query: null, incident_ids: [5], quality_issue_ids: [31],
    recommended_actions: [], metrics: {}, ...overrides,
  };
}

export function briefing(overrides: Partial<Briefing> = {}): Briefing {
  return {
    period: {
      mode: "shift", label: "Day shift, 15 Jan 2026 06:00 – 15 Jan 2026 14:00 (UTC)", start: "2026-01-15T06:00:00Z", end: "2026-01-15T14:00:00Z",
      timezone: "UTC", local_start: "2026-01-15T06:00:00+00:00", local_end: "2026-01-15T14:00:00+00:00",
      time_basis: "Events by occurrence time (occurred_at) in [start, end).", baseline: "2 comparison window(s)", default_reason: null,
    },
    generated_at: "2026-11-22T12:00:00Z",
    filters: { zones: [] },
    executive_summary: { text: "Day shift: 12 events from 6 devices. Needs attention first: #5 radar-int05 offline (critical, open).",
      generated_by: "deterministic_template", provider: "deterministic", validated: true, fallback_reason: null },
    key_metrics: { events_total: 12, devices_reporting: 6, zones_reporting: 3, incidents_active: 2, incidents_unresolved: 2, incidents_new: 2,
      incidents_immediate: 1, quality_issues_total: 4, duplicate_submissions_ignored: 1, events_per_hour: [{ hour_utc: "2026-01-15 10", count: 9 }] },
    priority_incidents: [{
      incident_id: 5, title: "radar-int05 offline", category: "device_failure", severity: "critical", status: "open", confidence: 0.8,
      confidence_level: "high", first_occurred_at: "2026-01-15T12:46:40Z", last_occurred_at: "2026-01-15T12:46:40Z", detected_at: "2026-01-15T13:00:00Z",
      event_count: 1, zones: ["Harbour-Rd-INT01"], devices: ["radar-int05"], sources: ["device_health"], conflict_count: 0, priority_score: 74.0,
      priority_rank: 1, immediate_attention: true,
      factors: [{ factor: "severity", points: 40, explanation: "Stored severity is critical." }, { factor: "status", points: 12, explanation: "Not yet acknowledged by an operator." }],
      reason: "Immediate attention: severity (+40); status (+12).", new_in_period: true, detected_after_period: false,
      evidence: [{ event_pk: 7, event_id: "h1" }], recommended_actions: ["Open a maintenance ticket for radar-int05."],
    }],
    unresolved_incidents: [], unresolved_total: 1, new_incidents: [], new_total: 0,
    sections: [
      { key: "device_health", title: "Device-health problems", summary: "1 device with health reports.", findings: [finding()], empty_reason: null },
      { key: "repeated_by_device", title: "Repeated events by device", summary: "None.", findings: [
        finding({ id: "repeated_by_device-1", category: "repeated_by_device", basis: "database_fact", statement: "Device acs-x: 14 events (access denied 14).",
          incident_ids: [], quality_issue_ids: [], evidence: [{ event_pk: 9, event_id: "rep-1" }], evidence_total: 14,
          event_query: { device_id: "acs-x", occurred_from: "2026-01-15T06:00:00+00:00", occurred_to: "2026-01-15T14:00:00+00:00" }, uncertainty: [] }),
      ], empty_reason: null },
      { key: "conflicts_uncertainty", title: "Conflicts and uncertainty", summary: "None.", findings: [],
        empty_reason: "No conflicts or uncertainty flags were found for this period." },
    ],
    recommended_actions: [finding({ id: "recommendation-1", category: "recommendation", basis: "recommendation",
      statement: "Dispatch maintenance to check radar-int05: a health problem was reported with no recovery in this period.", uncertainty: [], evidence: [] })],
    confidence: { level: "medium", score: 0.72, factors: [{ factor: "device_health", effect: -0.05, explanation: "1 device(s) reported a fault without recovery." }],
      limitations: ["Counts reflect the events stored when the briefing was generated; late arrivals can change them."] },
    evidence_index: [{ event_pk: 7, event_id: "h1" }, { event_pk: 9, event_id: "rep-1" }],
    incident_ids: [5], quality_issue_ids: [31],
    grounding: { events_referenced: 2, incidents_referenced: 1, quality_issues_referenced: 1, all_references_verified: true, unverified_references: [] },
    ...overrides,
  };
}
