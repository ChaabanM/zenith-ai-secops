import type { EventDetail } from "@/types/events";
import type { IncidentDetail, IncidentsSummary, IncidentSummary, TimelineEntry } from "@/types/incidents";

export function summaryItem(overrides: Partial<IncidentSummary> = {}): IncidentSummary {
  return {
    id: 706,
    title: "4 denied access attempts with credential CRD-27910 at Gate-C, then door forced open",
    category: "unauthorized_access",
    severity: "critical",
    confidence: 0.89,
    confidence_level: "high",
    status: "open",
    correlation_state: "active",
    first_occurred_at: "2026-01-14T22:56:06Z",
    last_occurred_at: "2026-01-14T23:01:04Z",
    detected_at: "2026-11-22T13:38:00Z",
    updated_at: "2026-11-22T13:38:00Z",
    event_count: 3,
    zones: ["Gate-C"],
    devices: ["acs-gate-c", "frs-gate-c"],
    sources: ["access_control", "face_recognition"],
    summary: "Credential CRD-27910 was denied 4 times at Gate-C within 5.0 min.",
    conflict_count: 1,
    rule_version: "4a.1",
    ...overrides,
  };
}

export const SUMMARY: IncidentsSummary = {
  total: 718,
  by_category: [
    { value: "traffic_enforcement", count: 357 },
    { value: "unauthorized_access", count: 149 },
  ],
  by_severity: [
    { value: "medium", count: 345 },
    { value: "high", count: 289 },
    { value: "critical", count: 81 },
    { value: "low", count: 3 },
  ],
  by_status: [
    { value: "open", count: 700 },
    { value: "acknowledged", count: 5 },
    { value: "investigating", count: 7 },
    { value: "resolved", count: 4 },
    { value: "false_positive", count: 2 },
  ],
  by_confidence: [],
  inactive: 0,
  unresolved: 712,
  unresolved_by_severity: [
    { value: "critical", count: 79 },
    { value: "high", count: 288 },
  ],
  last_run: null,
};

export function entry(overrides: Partial<TimelineEntry>): TimelineEntry {
  return {
    event_pk: 1,
    event_id: "evt-a",
    submission_id: 11,
    occurred_at: "2026-01-14T22:56:06Z",
    received_at: "2026-01-14T22:56:07Z",
    source_type: "access_control",
    device_id: "acs-gate-c",
    event_type: "access_denied",
    zone: "Gate-C",
    severity: "low",
    source_confidence: null,
    role: "access_attempt",
    link_type: "anchor",
    core: true,
    linkage_confidence: 1,
    linkage_explanation: "First denied attempt with credential CRD-27910.",
    link_description: "the event that defines the incident",
    low_source_confidence: false,
    conflict: null,
    ...overrides,
  };
}

/** Timeline deliberately out of chronological order (later event first, as if it arrived first). */
export function incidentDetail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    ...summaryItem(),
    severity_reason: "Highest severity among strongly linked evidence: critical (evt-door, door forced open).",
    confidence_reason: "Heuristic score (not a probability): core linkage strength 0.91.",
    linkage_explanation: "Linked by the same access credential in a continuous sequence of attempts (2).",
    observations: ["2026-01-14 22:56:06 UTC: acs-gate-c reported access denied at Gate-C"],
    inferences: ["The attempts are attributed to one credential, not to a verified person."],
    conflicts: ["badge holder EMP-1 vs face match EMP-2 (score 0.91)"],
    missing_information: ["Which identity is correct cannot be determined from the data."],
    uncertainty: ["evt-cam is linked by context only; it may be unrelated."],
    recommended_actions: ["Review camera footage of Gate-C for the attempt period.", "Consider dispatching a guard (advisory; requires operator decision)."],
    details: { core_events: 3, contextual_events: 1 },
    status_note: null,
    status_updated_at: null,
    superseded_by_id: null,
    lineage: null,
    merged_incident_ids: [],
    timeline: [
      entry({
        event_pk: 3, event_id: "evt-face", occurred_at: "2026-01-14T22:57:00Z", source_type: "face_recognition", device_id: "frs-gate-c",
        event_type: "face_match", source_confidence: 0.91, link_type: "unambiguous_pairing", linkage_confidence: 0.49,
        linkage_explanation: "The only face capture at Gate-C within 3 s of the only badge use.", link_description: "the only badge use and the only face capture",
        conflict: "badge holder EMP-1 vs face match EMP-2 (score 0.91)", role: "face_capture",
      }),
      entry({ event_pk: 1, event_id: "evt-a" }),
      entry({
        event_pk: 4, event_id: "evt-cam", occurred_at: "2026-01-14T22:58:00Z", source_type: "cctv", device_id: "cctv-gate-c", event_type: "person_detected",
        source_confidence: 0.42, low_source_confidence: true, core: false, link_type: "contextual", linkage_confidence: 0.55,
        linkage_explanation: "Same zone within 2 min.", link_description: "same zone and a compatible event type", role: "context",
      }),
      entry({ event_pk: 2, event_id: "evt-b", occurred_at: "2026-01-14T22:56:30Z", link_type: "same_credential", linkage_confidence: 0.9 }),
    ],
    timeline_total: 4,
    low_source_confidence_threshold: 0.6,
    quality_issues: [
      { id: 4107, issue_type: "conflicting_sensor_results", subtype: "identity", severity: "high", title: "Badge of EMP-1 used while face recognition matched EMP-2", event_ids: ["evt-a", "evt-face"] },
    ],
    ...overrides,
  };
}

export function eventDetail(id: number, eventId: string): EventDetail {
  const submission = {
    id: id + 10, batch_id: "batch-1", position: 1, external_event_id: eventId, status: "accepted" as const, duplicate_kind: null,
    conflicting_fields: null, errors: [], warnings: [], normalized_event_id: id, ingested_at: "2026-01-14T22:56:07Z",
    content_sha256: "abc", raw_size_bytes: 42, raw_truncated: false, raw_content: JSON.stringify({ event_id: eventId, raw_marker: "ORIGINAL-RAW" }),
  };
  return {
    id, event_id: eventId, source_type: "access_control", device_id: "acs-gate-c", event_type: "access_denied",
    occurred_at: "2026-01-14T22:56:06Z", received_at: "2026-01-14T22:56:07Z", ingested_at: "2026-01-14T22:56:08Z", delay_seconds: 1,
    location: { latitude: 25.2, longitude: 55.3, zone: "Gate-C" }, severity: "low", confidence: null,
    payload: { credential_id: "CRD-27910" }, content_sha256: "abc", submission, duplicate_submissions: [], quality_issues: [],
  };
}
