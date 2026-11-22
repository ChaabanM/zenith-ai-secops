import { Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import type { EventDetail } from "@/types/events";
import type { IncidentQualityIssue, TimelineEntry } from "@/types/incidents";

import { formatDateTime, humanize, sourceLabel } from "../events/format";
import { issueTypeLabel } from "../quality/labels";
import { CONFLICT_ISSUE_TYPES, formatScore } from "./labels";

const MAX_SIDES = 4;

/**
 * Contradictory records side by side. The groups come from backend data only:
 * conflict-type data-quality findings that reference two or more of this incident's
 * events, plus any timeline entry the engine marked with a conflict. No side is
 * ever chosen as the correct one.
 */
export function ConflictComparison({
  timeline,
  qualityIssues,
  onOpenEvent,
  onOpenIssue,
}: {
  timeline: TimelineEntry[];
  qualityIssues: IncidentQualityIssue[];
  onOpenEvent: (pk: number) => void;
  onOpenIssue: (id: number) => void;
}) {
  const byId = new Map(timeline.map((t) => [t.event_id, t]));
  const groups: { key: string; title: string; issueId?: number; entries: TimelineEntry[] }[] = [];
  const covered = new Set<string>();
  for (const q of qualityIssues) {
    if (!CONFLICT_ISSUE_TYPES.has(q.issue_type)) continue;
    const entries = q.event_ids.map((id) => byId.get(id)).filter((t): t is TimelineEntry => !!t);
    if (entries.length < 2) continue;
    entries.forEach((t) => covered.add(t.event_id));
    groups.push({ key: `q${q.id}`, title: `${issueTypeLabel(q.issue_type)}: ${q.title}`, issueId: q.id, entries: entries.slice(0, MAX_SIDES) });
  }
  for (const t of timeline) {
    if (!t.conflict || covered.has(t.event_id)) continue;
    const anchor = timeline.find((x) => x.link_type === "anchor" && x.event_pk !== t.event_pk);
    groups.push({ key: `e${t.event_pk}`, title: t.conflict, entries: anchor ? [anchor, t] : [t] });
  }
  if (groups.length === 0) return null;
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <ConflictGroup key={g.key} title={g.title} issueId={g.issueId} entries={g.entries} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
      ))}
    </div>
  );
}

function ConflictGroup({
  title,
  issueId,
  entries,
  onOpenEvent,
  onOpenIssue,
}: {
  title: string;
  issueId?: number;
  entries: TimelineEntry[];
  onOpenEvent: (pk: number) => void;
  onOpenIssue: (id: number) => void;
}) {
  const [details, setDetails] = useState<(EventDetail | null)[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setDetails(await Promise.all(entries.map((e) => api.getEvent(e.event_pk).catch(() => null))));
    setLoading(false);
  }

  const keys = details ? [...new Set(details.flatMap((d) => (d ? Object.keys(d.payload) : [])))].sort() : [];
  const differs = (key: string) =>
    new Set(details?.map((d) => (d ? JSON.stringify(d.payload[key] ?? null) : "unavailable"))).size > 1;

  return (
    <div className="rounded-md border border-red-300/70 p-3" data-testid="conflict-group">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        <div className="flex gap-1">
          {issueId !== undefined && (
            <Button variant="ghost" size="sm" onClick={() => onOpenIssue(issueId)}>
              Quality #{issueId}
            </Button>
          )}
          {!details && (
            <Button variant="outline" size="sm" onClick={load} disabled={loading}>
              {loading && <Loader2 className="animate-spin" />} Compare reported values
            </Button>
          )}
        </div>
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[28rem] text-left text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="w-36 py-1 pr-2 font-medium" />
              {entries.map((e) => (
                <th key={e.event_pk} className="py-1 pr-2 font-medium">
                  <button className="font-mono underline underline-offset-4" onClick={() => onOpenEvent(e.event_pk)}>
                    {e.event_id}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="align-top">
            <CompareRow label="Source" highlight={false} values={entries.map((e) => sourceLabel(e.source_type))} />
            <CompareRow label="Device" highlight={false} values={entries.map((e) => e.device_id)} mono />
            <CompareRow label="Event type" highlight={false} values={entries.map((e) => humanize(e.event_type))} />
            <CompareRow label="Occurred" values={entries.map((e) => formatDateTime(e.occurred_at))} />
            <CompareRow label="Zone" values={entries.map((e) => e.zone ?? "—")} />
            <CompareRow label="Sensor confidence" highlight={false} values={entries.map((e) => (e.source_confidence === null ? "not reported" : formatScore(e.source_confidence)))} />
            {details &&
              keys.map((k) => (
                <CompareRow
                  key={k}
                  label={humanize(k)}
                  highlight={differs(k)}
                  values={details.map((d) => (d ? display(d.payload[k]) : "unavailable"))}
                />
              ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Values are shown as each sensor reported them. The system does not decide which record is correct.
        {details && " Highlighted rows differ between records."}
      </p>
    </div>
  );
}

function display(value: unknown): string {
  if (value === undefined) return "—";
  if (value === null) return "null (unknown)";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function CompareRow({ label, values, mono, highlight }: { label: string; values: string[]; mono?: boolean; highlight?: boolean }) {
  const differ = highlight ?? new Set(values).size > 1;
  return (
    <tr className={cn("border-t", differ && "bg-red-50/70 dark:bg-red-500/10")}>
      <th className="py-1 pr-2 font-normal text-muted-foreground">{label}</th>
      {values.map((v, i) => (
        <td key={i} className={cn("py-1 pr-2 break-words", mono && "font-mono", differ && "font-medium")}>
          {v}
        </td>
      ))}
    </tr>
  );
}
