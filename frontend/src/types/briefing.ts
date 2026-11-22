import type { Severity } from "@/types/events";

export type FindingBasis = "database_fact" | "observed" | "inference" | "conflict" | "missing" | "recommendation";
export type Level = "high" | "medium" | "low";

export interface EvidenceRef {
  event_pk: number;
  event_id: string;
}

export interface Finding {
  id: string;
  category: string;
  basis: FindingBasis;
  statement: string;
  reasoning: string;
  confidence: { level: Level; score: number | null; basis: string };
  uncertainty: string[];
  missing_information: string[];
  evidence: EvidenceRef[];
  evidence_total: number;
  event_query: Record<string, string> | null;
  incident_ids: number[];
  quality_issue_ids: number[];
  recommended_actions: string[];
  metrics: Record<string, unknown>;
}

export interface PriorityFactor {
  factor: string;
  points: number;
  explanation: string;
}

export interface PrioritizedIncident {
  incident_id: number;
  title: string;
  category: string;
  severity: Severity;
  status: string;
  confidence: number;
  confidence_level: Level;
  first_occurred_at: string;
  last_occurred_at: string;
  detected_at: string;
  event_count: number;
  zones: string[];
  devices: string[];
  sources: string[];
  conflict_count: number;
  priority_score: number;
  priority_rank: number;
  immediate_attention: boolean;
  factors: PriorityFactor[];
  reason: string;
  new_in_period: boolean;
  detected_after_period: boolean;
  evidence: EvidenceRef[];
  recommended_actions: string[];
}

export interface BriefingSection {
  key: string;
  title: string;
  summary: string;
  findings: Finding[];
  empty_reason: string | null;
}

export interface Briefing {
  period: {
    mode: "shift" | "day" | "custom";
    label: string;
    start: string;
    end: string;
    timezone: string;
    local_start: string;
    local_end: string;
    time_basis: string;
    baseline: string;
    default_reason: string | null;
  };
  generated_at: string;
  filters: { zones: string[] };
  executive_summary: {
    text: string;
    generated_by: "deterministic_template" | "local_language_model";
    provider: string;
    validated: boolean;
    fallback_reason: string | null;
  };
  key_metrics: Record<string, unknown> & {
    events_total: number;
    devices_reporting: number;
    zones_reporting: number;
    incidents_active: number;
    incidents_unresolved: number;
    incidents_new: number;
    incidents_immediate: number;
    quality_issues_total?: number;
    duplicate_submissions_ignored: number;
    events_per_hour: { hour_utc: string; count: number }[];
  };
  priority_incidents: PrioritizedIncident[];
  unresolved_incidents: PrioritizedIncident[];
  unresolved_total: number;
  new_incidents: PrioritizedIncident[];
  new_total: number;
  sections: BriefingSection[];
  recommended_actions: Finding[];
  confidence: { level: Level; score: number; factors: { factor: string; effect: number; explanation: string }[]; limitations: string[] };
  evidence_index: EvidenceRef[];
  incident_ids: number[];
  quality_issue_ids: number[];
  grounding: {
    events_referenced: number;
    incidents_referenced: number;
    quality_issues_referenced: number;
    all_references_verified: boolean;
    unverified_references: string[];
  };
}

export interface BriefingPeriods {
  timezone: string;
  shifts: { name: string; label: string; start: string }[];
  data_start: string | null;
  data_end: string | null;
  max_period_days: number;
  time_basis: string;
}

export interface BriefingQuery {
  mode?: "shift" | "day" | "custom";
  date?: string;
  shift?: string;
  start?: string;
  end?: string;
  tz?: string;
  zone?: string;
}
