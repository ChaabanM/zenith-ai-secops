import { ExternalLink } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { Collapsible, ShortId } from "@/components/evidence";
import { cn } from "@/lib/utils";
import type { EvidenceRef, Finding, FindingBasis, Level } from "@/types/briefing";

export const BASIS_LABELS: Record<FindingBasis, string> = {
  database_fact: "Database fact",
  observed: "Reported by sensors",
  inference: "System inference",
  conflict: "Conflict",
  missing: "Missing information",
  recommendation: "Recommendation",
};

const BASIS_HINTS: Record<FindingBasis, string> = {
  database_fact: "An exact count or aggregate of the stored records.",
  observed: "What devices or operators reported. Not independently verified.",
  inference: "A conclusion drawn by the system (rules or a statistical comparison), not observed directly.",
  conflict: "Records that contradict each other. The system does not decide which is correct.",
  missing: "Evidence that is absent. Absence of reports is not confirmation that everything was normal.",
  recommendation: "Advisory only. Nothing is triggered automatically.",
};

const BASIS_STYLES: Record<FindingBasis, string> = {
  database_fact: "border-slate-300 bg-slate-100 text-slate-800 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100",
  observed: "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-500/40 dark:bg-sky-500/15 dark:text-sky-100",
  inference: "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-500/40 dark:bg-violet-500/15 dark:text-violet-100",
  conflict: "border-red-300 bg-red-50 text-red-900 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-100",
  missing: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-100",
  recommendation: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-100",
};

export function BasisBadge({ basis }: { basis: FindingBasis }) {
  return (
    <span
      className={cn("inline-flex shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold tracking-wide uppercase", BASIS_STYLES[basis])}
      title={BASIS_HINTS[basis]}
      data-basis={basis}
    >
      {BASIS_LABELS[basis]}
    </span>
  );
}

export function LevelText({ level }: { level: Level }) {
  return <span className={cn("font-medium", level === "low" && "text-amber-700 dark:text-amber-300")}>{level}</span>;
}

interface RefProps {
  onOpenEvent: (pk: number) => void;
  onOpenIssue: (id: number) => void;
}

export function EventChips({ refs, onOpenEvent }: { refs: EvidenceRef[]; onOpenEvent: (pk: number) => void }) {
  return (
    <>
      {refs.map((r) => (
        <button
          key={r.event_pk}
          onClick={() => onOpenEvent(r.event_pk)}
          className="rounded border px-1.5 py-0.5 font-mono text-[11px] hover:bg-muted"
          aria-label={`Open source event ${r.event_id}`}
          title={r.event_id}
        >
          <ShortId value={r.event_id} />
        </button>
      ))}
    </>
  );
}

export function IncidentLink({ id, label = "Back to briefing" }: { id: number; label?: string }) {
  const location = useLocation();
  const search = new URLSearchParams(location.search);
  search.delete("event"); // detail panels are not part of the briefing's own state
  search.delete("issue");
  const back = `${location.pathname}${search.toString() ? `?${search}` : ""}`;
  return (
    <Link
      to={`/incidents/${id}`}
      state={{ backTo: { to: back, label } }}
      className="rounded border px-1.5 py-0.5 text-[11px] font-medium hover:bg-muted"
    >
      Incident #{id}
    </Link>
  );
}

export function FindingCard({ finding: f, onOpenEvent, onOpenIssue }: { finding: Finding } & RefProps) {
  const hasRefs = f.evidence.length || f.incident_ids.length || f.quality_issue_ids.length || f.event_query;
  return (
    <li className="space-y-1.5 rounded-md border p-3" data-testid="finding" data-finding-id={f.id}>
      <div className="flex flex-wrap items-start gap-2">
        <BasisBadge basis={f.basis} />
        <p className="min-w-0 flex-1 text-sm leading-relaxed">{f.statement}</p>
      </div>
      <p className="text-xs text-muted-foreground">
        Confidence <LevelText level={f.confidence.level} /> ({f.confidence.basis})
      </p>
      <Collapsible label="Why the system says this" testId="finding-reasoning">
        <p className="text-xs leading-relaxed text-muted-foreground">{f.reasoning}</p>
      </Collapsible>
      {(f.uncertainty.length > 0 || f.missing_information.length > 0) && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-800 dark:text-amber-300">
          {f.uncertainty.map((u, i) => (
            <li key={`u${i}`}>Uncertain: {u}</li>
          ))}
          {f.missing_information.map((m, i) => (
            <li key={`m${i}`}>Missing: {m}</li>
          ))}
        </ul>
      )}
      {hasRefs ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">Evidence:</span>
          {f.incident_ids.slice(0, 12).map((id) => (
            <IncidentLink key={id} id={id} />
          ))}
          {f.incident_ids.length > 12 && <span className="text-muted-foreground">+{f.incident_ids.length - 12} incidents</span>}
          {f.quality_issue_ids.slice(0, 8).map((id) => (
            <button key={id} onClick={() => onOpenIssue(id)} className="rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted">
              Quality #{id}
            </button>
          ))}
          <EventChips refs={f.evidence} onOpenEvent={onOpenEvent} />
          {f.event_query && (
            <Link
              to={`/events?${new URLSearchParams(f.event_query).toString()}`}
              className="inline-flex items-center gap-1 text-[11px] underline underline-offset-4"
            >
              All {f.evidence_total.toLocaleString()} events <ExternalLink className="size-3" />
            </Link>
          )}
          {!f.event_query && f.evidence_total > f.evidence.length && f.evidence.length > 0 && (
            <span className="text-muted-foreground">
              showing {f.evidence.length} of {f.evidence_total.toLocaleString()} events
            </span>
          )}
        </div>
      ) : null}
    </li>
  );
}
