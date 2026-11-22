export const SEVERITIES = ["critical", "high", "medium", "low", "informational"] as const;
export type Severity = (typeof SEVERITIES)[number];

export interface EventLocation {
  latitude: number | null;
  longitude: number | null;
  zone: string | null;
}

export interface EventSummary {
  id: number;
  event_id: string;
  source_type: string;
  device_id: string;
  event_type: string;
  occurred_at: string;
  received_at: string | null;
  ingested_at: string;
  delay_seconds: number | null;
  location: EventLocation;
  severity: Severity | null;
  confidence: number | null;
}

export interface ValidationIssue {
  field: string;
  code: string;
  message: string;
}

export type SubmissionStatus = "accepted" | "invalid" | "duplicate";

export interface Submission {
  id: number;
  batch_id: string;
  position: number;
  external_event_id: string | null;
  status: SubmissionStatus;
  duplicate_kind: "exact" | "conflicting" | null;
  conflicting_fields: string[] | null;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  normalized_event_id: number | null;
  ingested_at: string;
  content_sha256: string;
  raw_size_bytes: number;
  raw_truncated: boolean;
  raw_content: string;
}

export interface EventDetail extends EventSummary {
  payload: Record<string, unknown>;
  content_sha256: string;
  submission: Submission;
  duplicate_submissions: Submission[];
  quality_issues: { id: number; issue_type: string; severity: Severity; title: string }[];
}

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_next: boolean;
}

export interface FacetValue {
  value: string;
  count: number;
}

export interface EventFacets {
  total_events: number;
  sources: FacetValue[];
  severities: FacetValue[];
  zones: FacetValue[];
  devices: FacetValue[];
  occurred_min: string | null;
  occurred_max: string | null;
}

export type EventSort = "-occurred_at" | "occurred_at" | "-id" | "id";

export interface EventQuery {
  page: number;
  page_size: number;
  sort: EventSort;
  severity?: string;
  source_type?: string;
  device_id?: string;
  zone?: string;
  occurred_from?: string;
  occurred_to?: string;
}

export interface RecordReport {
  position: number;
  submission_id: number;
  event_id: string | null;
  status: SubmissionStatus;
  duplicate_kind: "exact" | "conflicting" | null;
  conflicting_fields: string[] | null;
  normalized_event_id: number | null;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface IngestSummary {
  batch_id: string;
  status: string;
  channel: "json" | "jsonl";
  filename: string | null;
  total_received: number;
  accepted: number;
  invalid: number;
  duplicate_event_ids: number;
  exact_duplicates: number;
  conflicting_duplicates: number;
  with_warnings: number;
  duration_ms: number;
  records: RecordReport[];
  records_truncated: boolean;
}
