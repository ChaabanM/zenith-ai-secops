import type { IncidentStatus, TimelineEntry } from "@/types/incidents";

/** Display names only. Every value shown comes from the backend; nothing here changes meaning. */
export const CATEGORY_LABELS: Record<string, string> = {
  traffic_enforcement: "Traffic enforcement",
  unauthorized_access: "Unauthorized access",
  perimeter_intrusion: "Perimeter intrusion",
  suspicious_activity: "Suspicious activity",
  emergency: "Emergency",
  device_failure: "Device failure",
  cyber_security: "Cyber security",
  fleet_anomaly: "Fleet anomaly",
};

export function categoryLabel(value: string): string {
  return CATEGORY_LABELS[value] ?? value.replace(/_/g, " ");
}

export const STATUS_LABELS: Record<IncidentStatus, string> = {
  open: "New / open",
  acknowledged: "Acknowledged",
  investigating: "Investigating",
  resolved: "Resolved",
  false_positive: "False positive",
};

export const STATUS_STYLES: Record<IncidentStatus, string> = {
  open: "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-500/40 dark:bg-blue-500/15 dark:text-blue-100",
  acknowledged: "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-500/40 dark:bg-violet-500/15 dark:text-violet-100",
  investigating: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-100",
  resolved: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-100",
  false_positive: "border-zinc-300 bg-zinc-100 text-zinc-700 dark:border-zinc-500/40 dark:bg-zinc-500/15 dark:text-zinc-200",
};

/** Operator actions offered in the UI. Each maps to one status accepted by PATCH /incidents/{id}. */
export const STATUS_ACTIONS: { status: IncidentStatus; label: string }[] = [
  { status: "acknowledged", label: "Acknowledge" },
  { status: "investigating", label: "Start investigating" },
  { status: "resolved", label: "Resolve" },
  { status: "false_positive", label: "Mark false positive" },
  { status: "open", label: "Reopen" },
];

/**
 * What kind of basis a link type rests on, so the operator can tell identifier-based
 * links from ones inferred from time and place. Keys are the backend link types.
 */
export const LINK_BASIS: Record<string, string> = {
  anchor: "Defines the incident",
  explicit_reference: "Explicit record reference",
  same_affected_device: "Same affected device",
  same_plate_same_site: "Shared plate (same site)",
  same_credential: "Shared credential",
  same_source_and_account: "Shared source address and account",
  same_door: "Same door",
  plausible_movement: "Shared plate (onward movement)",
  same_identifier_conflict: "Shared identifier, conflicting context",
  unambiguous_pairing: "Time and place pairing only (no shared identifier)",
  contextual: "Time, place and event type only",
  appearance_match: "Appearance match only",
  affected_output: "Output of the failing device",
};

export function linkBasis(linkType: string): string {
  return LINK_BASIS[linkType] ?? linkType.replace(/_/g, " ");
}

export type EvidenceClass = "conflict" | "uncertain" | "strong" | "contextual";

export const EVIDENCE_CLASS_LABELS: Record<EvidenceClass, string> = {
  strong: "Strong link",
  contextual: "Contextual",
  conflict: "Conflicting",
  uncertain: "Uncertain",
};

export const EVIDENCE_CLASS_HINTS: Record<EvidenceClass, string> = {
  strong: "Linked by a shared identifier or an explicit reference (backend core evidence).",
  contextual: "Attached by time, place and event type only. It may be unrelated and never links further events.",
  conflict: "This record contradicts other evidence in the incident. Neither side is treated as correct.",
  uncertain: "Low sensor-reported confidence, or degraded output of a failing device. Do not treat as verified.",
};

/**
 * Presentation class of a timeline entry, derived only from backend fields.
 * Conflicts take precedence, then uncertainty, so a weak record is never shown as a plain strong link.
 */
export function evidenceClass(entry: Pick<TimelineEntry, "conflict" | "low_source_confidence" | "link_type" | "core">): EvidenceClass {
  if (entry.conflict) return "conflict";
  if (entry.low_source_confidence || entry.link_type === "affected_output") return "uncertain";
  return entry.core ? "strong" : "contextual";
}

export const EVIDENCE_CLASS_STYLES: Record<EvidenceClass, { badge: string; rail: string }> = {
  strong: {
    badge: "border-slate-400 bg-slate-800 text-white dark:border-slate-300 dark:bg-slate-200 dark:text-slate-900",
    rail: "bg-slate-700 dark:bg-slate-300",
  },
  contextual: {
    badge: "border-dashed border-slate-400 bg-transparent text-slate-700 dark:text-slate-300",
    rail: "bg-slate-300 dark:bg-slate-600",
  },
  conflict: {
    badge: "border-red-300 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-200",
    rail: "bg-red-500",
  },
  uncertain: {
    badge: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200",
    rail: "bg-amber-400",
  },
};

/** Quality issue types that describe contradictory records (shown side by side). */
export const CONFLICT_ISSUE_TYPES = new Set(["conflicting_sensor_results", "timestamp_conflict", "location_conflict"]);

export function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toFixed(2);
}
