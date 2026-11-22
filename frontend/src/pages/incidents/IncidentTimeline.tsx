import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import type { IncidentQualityIssue, TimelineEntry } from "@/types/incidents";

import { SeverityBadge } from "../events/EventsTable";
import { formatDateTime, formatDelay, humanize, sourceLabel } from "../events/format";
import { issueTypeLabel } from "../quality/labels";
import { EvidenceClassBadge } from "./badges";
import {
  EVIDENCE_CLASS_HINTS,
  EVIDENCE_CLASS_LABELS,
  EVIDENCE_CLASS_STYLES,
  evidenceClass,
  formatScore,
  linkBasis,
  type EvidenceClass,
} from "./labels";

const PAGE = 100;
const CLASSES: EvidenceClass[] = ["strong", "contextual", "conflict", "uncertain"];

/** Chronological order by event time, ties broken by internal event id (never arrival order). */
export function sortTimeline(entries: TimelineEntry[]): TimelineEntry[] {
  return [...entries].sort((a, b) => {
    const diff = Date.parse(a.occurred_at) - Date.parse(b.occurred_at);
    return diff !== 0 ? diff : a.event_pk - b.event_pk;
  });
}

interface Props {
  entries: TimelineEntry[];
  total: number;
  threshold: number;
  qualityIssues: IncidentQualityIssue[];
  selectedEvent: number | null;
  onOpenEvent: (eventPk: number) => void;
  onOpenIssue: (issueId: number) => void;
}

export function IncidentTimeline({ entries, total, threshold, qualityIssues, selectedEvent, onOpenEvent, onOpenIssue }: Props) {
  const ordered = useMemo(() => sortTimeline(entries), [entries]);
  const [hidden, setHidden] = useState<Set<EvidenceClass>>(new Set());
  const [limit, setLimit] = useState(PAGE);
  const issuesByEvent = useMemo(() => {
    const map = new Map<string, IncidentQualityIssue[]>();
    for (const q of qualityIssues) for (const id of q.event_ids) map.set(id, [...(map.get(id) ?? []), q]);
    return map;
  }, [qualityIssues]);
  const counts = useMemo(() => {
    const c: Record<EvidenceClass, number> = { strong: 0, contextual: 0, conflict: 0, uncertain: 0 };
    for (const e of ordered) c[evidenceClass(e)] += 1;
    return c;
  }, [ordered]);
  const visible = ordered.filter((e) => !hidden.has(evidenceClass(e)));

  const toggle = (k: EvidenceClass) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs" role="group" aria-label="Show evidence types">
        {CLASSES.map((k) => (
          <button
            key={k}
            onClick={() => toggle(k)}
            aria-pressed={!hidden.has(k)}
            title={EVIDENCE_CLASS_HINTS[k]}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 transition-opacity",
              hidden.has(k) && "opacity-40",
            )}
          >
            <span className={cn("inline-block h-3 w-1 rounded-full", EVIDENCE_CLASS_STYLES[k].rail)} />
            {EVIDENCE_CLASS_LABELS[k]} <span className="tabular-nums text-muted-foreground">{counts[k]}</span>
          </button>
        ))}
        <span className="text-muted-foreground">
          Ordered by when each event occurred (not when it arrived). Sensor confidence is the device's own value; link strength is
          a heuristic score.
        </span>
      </div>

      <ol className="space-y-2" aria-label="Incident timeline">
        {visible.slice(0, limit).map((e) => (
          <TimelineItem
            key={e.event_pk}
            entry={e}
            threshold={threshold}
            issues={issuesByEvent.get(e.event_id) ?? []}
            selected={selectedEvent === e.event_pk}
            onOpen={() => onOpenEvent(e.event_pk)}
            onOpenIssue={onOpenIssue}
          />
        ))}
      </ol>
      {visible.length === 0 && <p className="text-sm text-muted-foreground">No entries of the selected types.</p>}
      {visible.length > limit && (
        <button className="text-sm underline underline-offset-4" onClick={() => setLimit((l) => l + PAGE)}>
          Show {Math.min(PAGE, visible.length - limit)} more of {visible.length - limit} remaining
        </button>
      )}
      {total > entries.length && (
        <p className="text-xs text-muted-foreground">
          The backend returned the first {entries.length.toLocaleString()} of {total.toLocaleString()} linked events.
        </p>
      )}
    </div>
  );
}

function TimelineItem({
  entry: e,
  threshold,
  issues,
  selected,
  onOpen,
  onOpenIssue,
}: {
  entry: TimelineEntry;
  threshold: number;
  issues: IncidentQualityIssue[];
  selected: boolean;
  onOpen: () => void;
  onOpenIssue: (id: number) => void;
}) {
  const kind = evidenceClass(e);
  const arrival = e.received_at ? (Date.parse(e.received_at) - Date.parse(e.occurred_at)) / 1000 : null;
  return (
    <li
      className={cn("relative flex overflow-hidden rounded-md border bg-card", selected && "ring-2 ring-ring/60", kind === "contextual" && "border-dashed")}
      data-testid="timeline-entry"
      data-event-id={e.event_id}
      data-evidence-class={kind}
    >
      <span className={cn("w-1.5 shrink-0", EVIDENCE_CLASS_STYLES[kind].rail)} aria-hidden />
      <div className="min-w-0 flex-1 space-y-1.5 p-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <time className="text-sm font-medium tabular-nums" dateTime={e.occurred_at}>
            {formatDateTime(e.occurred_at)}
          </time>
          <EvidenceClassBadge kind={kind} />
          <SeverityBadge severity={e.severity} />
          <button
            onClick={onOpen}
            className="font-mono text-xs underline underline-offset-4 hover:text-foreground"
            aria-label={`Open source event ${e.event_id}`}
          >
            {e.event_id}
          </button>
        </div>
        <p className="text-sm">
          <span className="font-medium">{sourceLabel(e.source_type)}</span> · {humanize(e.event_type)} ·{" "}
          <span className="font-mono text-xs">{e.device_id}</span> · {e.zone ?? <span className="italic text-muted-foreground">no zone</span>}
        </p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-0.5 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="inline text-muted-foreground">Role: </dt>
            <dd className="inline">{humanize(e.role)}</dd>
          </div>
          <div>
            <dt className="inline text-muted-foreground">Link basis: </dt>
            <dd className="inline">{linkBasis(e.link_type)}</dd>
          </div>
          <div>
            <dt className="inline text-muted-foreground">Link strength (heuristic): </dt>
            <dd className="inline tabular-nums">{formatScore(e.linkage_confidence)}</dd>
          </div>
          <div>
            <dt className="inline text-muted-foreground">Sensor-reported confidence: </dt>
            <dd className={cn("inline tabular-nums", e.low_source_confidence && "font-medium text-amber-700 dark:text-amber-300")}>
              {e.source_confidence === null ? "not reported" : formatScore(e.source_confidence)}
              {e.low_source_confidence && ` (below ${formatScore(threshold)})`}
            </dd>
          </div>
        </dl>
        <p className="text-sm text-muted-foreground">
          <span className="text-foreground">Why linked: </span>
          {e.linkage_explanation}
          <span className="text-xs"> — {e.link_description}.</span>
        </p>
        {e.conflict && (
          <p className="rounded border border-red-300/70 bg-red-50 px-2 py-1 text-sm text-red-900 dark:bg-red-500/10 dark:text-red-200">
            <span className="font-medium">Conflict: </span>
            {e.conflict}
          </p>
        )}
        {kind === "uncertain" && (
          <p className="text-xs text-amber-800 dark:text-amber-300">
            {e.link_type === "affected_output"
              ? "Output of a device during its reported degradation: it may be unreliable and is not identity evidence."
              : "Low sensor-reported confidence: not verified identity evidence."}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {arrival !== null && Math.abs(arrival) >= 60 && (
            <span className="text-amber-700 dark:text-amber-300">Received {formatDelay(arrival)} after it occurred</span>
          )}
          {issues.map((q) => (
            <button
              key={q.id}
              onClick={() => onOpenIssue(q.id)}
              className="rounded border px-1.5 py-0.5 hover:bg-muted"
              title={q.title}
            >
              Quality #{q.id}: {issueTypeLabel(q.issue_type)}
            </button>
          ))}
          <span className="text-muted-foreground">Submission #{e.submission_id}</span>
        </div>
      </div>
    </li>
  );
}
