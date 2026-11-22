import type { Briefing, Finding, PrioritizedIncident } from "@/types/briefing";
import type { Severity } from "@/types/events";

export interface CountItem {
  value: string;
  count: number;
}

export interface DashboardSummary {
  period: Briefing["period"] & { query: Record<string, string> };
  generated_at: string;
  filters: { zones: string[] };
  scope: Record<"period" | "status" | "stored_totals" | "scores", string>;
  period_metrics: {
    events_total: number;
    devices_reporting: number;
    zones_reporting: number;
    incidents_active: number;
    incidents_unresolved: number;
    incidents_new: number;
    incidents_high_critical: number;
    incidents_unresolved_high_critical: number;
    incidents_by_severity: Record<string, number>;
    quality_issues_total: number;
    duplicate_submissions_ignored: number;
    devices_with_health_reports: number;
    devices_without_reported_recovery: number;
    devices_with_volume_drop: number;
    device_health_concerns: number;
    latest_stored_event_at: string | null;
    period_extends_past_stored_data: boolean;
  };
  stored_totals: {
    events_stored: number;
    submissions_invalid: number;
    submissions_duplicate: number;
    incidents_active: number;
    incidents_unresolved: number;
    incidents_unresolved_high_critical: number;
    quality_issues: number;
    data_start: string | null;
    data_end: string | null;
  };
  priority_incidents: PrioritizedIncident[];
  events_by_source: CountItem[];
  events_per_hour: { hour_utc: string; count: number }[];
  spikes: Finding[];
  quality_by_type: CountItem[];
  quality_high_severity: { id: number; issue_type: string; severity: Severity; title: string; occurred_from: string; occurred_to: string; event_count: number }[];
  quality_high_severity_total: number;
  uncertainty: Record<"low_confidence_detections" | "unlinkable_events" | "conflicting_record_sets" | "incidents_with_conflicts" | "delayed_events", number>;
  device_health: Finding[];
  device_health_note: string;
  briefing: {
    executive_summary: Briefing["executive_summary"];
    recommendations: Finding[];
    recommendations_total: number;
    confidence: Briefing["confidence"];
  };
  grounding: Briefing["grounding"];
}

export interface DashboardQuery {
  mode?: "shift" | "day";
  date?: string;
  shift?: string;
  tz?: string;
  zone?: string;
}
