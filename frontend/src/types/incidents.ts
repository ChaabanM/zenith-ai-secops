import type { FacetValue, Severity } from "@/types/events";

/** Operator-maintained investigation status (PATCH /incidents/{id}); the correlation engine never changes it. */
export const INCIDENT_STATUSES = ["open", "acknowledged", "investigating", "resolved", "false_positive"] as const;
export type IncidentStatus = (typeof INCIDENT_STATUSES)[number];
export const UNRESOLVED_STATUSES: IncidentStatus[] = ["open", "acknowledged", "investigating"];

export const INCIDENT_CATEGORIES = [
  "traffic_enforcement",
  "unauthorized_access",
  "perimeter_intrusion",
  "suspicious_activity",
  "emergency",
  "device_failure",
  "cyber_security",
  "fleet_anomaly",
] as const;

export type ConfidenceLevel = "high" | "medium" | "low";

export interface IncidentSummary {
  id: number;
  title: string;
  category: string;
  severity: Severity;
  /** Heuristic score in [0, 1]; not a calibrated probability. */
  confidence: number;
  confidence_level: ConfidenceLevel;
  status: IncidentStatus;
  correlation_state: "active" | "merged" | "obsolete";
  first_occurred_at: string;
  last_occurred_at: string;
  detected_at: string;
  updated_at: string;
  event_count: number;
  zones: string[];
  devices: string[];
  sources: string[];
  summary: string;
  conflict_count: number;
  rule_version: string;
}

export interface TimelineEntry {
  event_pk: number;
  event_id: string;
  submission_id: number;
  occurred_at: string;
  received_at: string | null;
  source_type: string;
  device_id: string;
  event_type: string;
  zone: string | null;
  severity: Severity | null;
  source_confidence: number | null;
  role: string;
  link_type: string;
  core: boolean;
  linkage_confidence: number;
  linkage_explanation: string;
  link_description: string;
  low_source_confidence: boolean;
  conflict: string | null;
}

export interface IncidentQualityIssue {
  id: number;
  issue_type: string;
  subtype: string | null;
  severity: Severity;
  title: string;
  event_ids: string[];
}

export interface IncidentDetail extends IncidentSummary {
  severity_reason: string;
  confidence_reason: string;
  linkage_explanation: string;
  observations: string[];
  inferences: string[];
  conflicts: string[];
  missing_information: string[];
  uncertainty: string[];
  recommended_actions: string[];
  details: Record<string, unknown>;
  status_note: string | null;
  status_updated_at: string | null;
  superseded_by_id: number | null;
  lineage: Record<string, unknown> | null;
  merged_incident_ids: number[];
  timeline: TimelineEntry[];
  timeline_total: number;
  low_source_confidence_threshold: number;
  quality_issues: IncidentQualityIssue[];
}

export interface CorrelationRun {
  id: number;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  observations_loaded: number;
  incidents_active: number;
  created: number;
  updated: number;
  unchanged: number;
  merged: number;
  obsoleted: number;
  error: string | null;
}

export interface IncidentsSummary {
  total: number;
  by_category: FacetValue[];
  by_severity: FacetValue[];
  by_status: FacetValue[];
  by_confidence: FacetValue[];
  inactive: number;
  unresolved: number;
  unresolved_by_severity: FacetValue[];
  last_run: CorrelationRun | null;
}

export type IncidentSort = "priority" | "-severity" | "-last_occurred_at" | "first_occurred_at" | "-confidence";

export interface IncidentQuery {
  page: number;
  page_size: number;
  sort: IncidentSort;
  severity?: string;
  status?: string;
  category?: string;
  source_type?: string;
  device_id?: string;
  zone?: string;
  occurred_from?: string;
  occurred_to?: string;
  event?: number;
}
