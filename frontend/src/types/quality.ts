import type { FacetValue, Severity, ValidationIssue } from "@/types/events";

export interface QualityIssueSummary {
  id: number;
  issue_type: string;
  subtype: string | null;
  severity: Severity;
  title: string;
  description: string;
  rule_id: string;
  rule_version: string;
  confidence: number | null;
  occurred_from: string;
  occurred_to: string;
  time_basis: "occurred_at" | "ingested_at";
  event_count: number;
  submission_count: number;
  devices: string[];
  zones: string[];
  sources: string[];
  status: string;
  first_detected_at: string;
  last_detected_at: string;
}

export interface EvidenceEvent {
  id: number;
  event_id: string;
  source_type: string;
  device_id: string;
  event_type: string;
  occurred_at: string;
  received_at: string | null;
  zone: string | null;
  latitude: number | null;
  longitude: number | null;
  severity: Severity | null;
  confidence: number | null;
}

export interface EvidenceSubmission {
  id: number;
  batch_id: string;
  position: number;
  status: string;
  duplicate_kind: string | null;
  external_event_id: string | null;
  errors: ValidationIssue[];
  ingested_at: string;
}

export interface EvidenceItem {
  role: string;
  external_event_id: string | null;
  event: EvidenceEvent | null;
  submission: EvidenceSubmission | null;
}

export interface QualityIssueDetail extends QualityIssueSummary {
  detection_reason: string;
  uncertainty: string | null;
  missing_evidence: string | null;
  details: Record<string, unknown>;
  evidence: EvidenceItem[];
  evidence_total: number;
  evidence_truncated: boolean;
}

export interface QualityRun {
  id: number;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  events_analyzed: number;
  submissions_analyzed: number;
  issues_total: number;
  inserted: number;
  updated: number;
  unchanged: number;
  removed: number;
  error: string | null;
}

export interface QualitySummary {
  total: number;
  by_type: FacetValue[];
  by_severity: FacetValue[];
  by_rule: FacetValue[];
  devices: FacetValue[];
  zones: FacetValue[];
  sources: FacetValue[];
  last_run: QualityRun | null;
  analysis_pending: boolean;
}

export type IssueSort = "-severity" | "-occurred_at" | "occurred_at" | "-id";

export interface IssueQuery {
  page: number;
  page_size: number;
  sort: IssueSort;
  issue_type?: string;
  severity?: string;
  source_type?: string;
  device_id?: string;
  zone?: string;
  occurred_from?: string;
  occurred_to?: string;
}

export interface EventQualityIssueRef {
  id: number;
  issue_type: string;
  severity: Severity;
  title: string;
}
