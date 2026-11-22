import { cn } from "@/lib/utils";
import type { ConfidenceLevel, IncidentStatus } from "@/types/incidents";

import {
  EVIDENCE_CLASS_HINTS,
  EVIDENCE_CLASS_LABELS,
  EVIDENCE_CLASS_STYLES,
  formatScore,
  STATUS_LABELS,
  STATUS_STYLES,
  type EvidenceClass,
} from "./labels";

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return (
    <span
      className={cn("inline-flex rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", STATUS_STYLES[status])}
      data-status={status}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

const LEVEL_STYLES: Record<ConfidenceLevel, string> = {
  high: "bg-slate-700 dark:bg-slate-200",
  medium: "bg-slate-400",
  low: "bg-amber-500",
};

/** Incident confidence: a heuristic score, shown as a number with its level, never as a percentage. */
export function ConfidenceScore({ value, level, compact = false }: { value: number; level: ConfidenceLevel; compact?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap tabular-nums"
      title="Heuristic confidence score in [0, 1]; not a calibrated probability"
    >
      <span className="inline-flex h-1.5 w-10 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className={cn("h-full", LEVEL_STYLES[level])} style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="font-medium">{formatScore(value)}</span>
      <span className={cn("text-xs text-muted-foreground", level === "low" && "text-amber-700 dark:text-amber-300")}>
        {compact ? level : `${level} (heuristic)`}
      </span>
    </span>
  );
}

export function EvidenceClassBadge({ kind }: { kind: EvidenceClass }) {
  return (
    <span
      className={cn("inline-flex rounded-md border px-1.5 py-0.5 text-[11px] font-semibold tracking-wide uppercase", EVIDENCE_CLASS_STYLES[kind].badge)}
      title={EVIDENCE_CLASS_HINTS[kind]}
      data-evidence-class={kind}
    >
      {EVIDENCE_CLASS_LABELS[kind]}
    </span>
  );
}
