import { CheckCircle2, ChevronDown, Loader2, Printer, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import type { Briefing, BriefingPeriods, BriefingQuery, PrioritizedIncident } from "@/types/briefing";
import type { EventFacets } from "@/types/events";

import { Collapsible } from "@/components/evidence";

import { inputClass } from "../events/controls";
import { EventDetailPanel } from "../events/EventDetailPanel";
import { SeverityBadge } from "../events/EventsTable";
import { formatDateTime, isoToZonedInput, zonedInputToIso } from "../events/format";
import { categoryLabel } from "../incidents/labels";
import { QualityIssueDetailPanel } from "../quality/QualityIssueDetailPanel";
import { EventChips, FindingCard, IncidentLink } from "./FindingCard";

const QUERY_KEYS = ["mode", "date", "shift", "start", "end", "tz", "zone"] as const;
const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

function readQuery(params: URLSearchParams): BriefingQuery {
  const q: BriefingQuery = {};
  for (const k of QUERY_KEYS) {
    const v = params.get(k);
    if (v) (q as Record<string, string>)[k] = v;
  }
  return q;
}

export function BriefingPage() {
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => readQuery(params), [params]);
  const [draft, setDraft] = useState<BriefingQuery>(query);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [periods, setPeriods] = useState<BriefingPeriods | null>(null);
  const [facets, setFacets] = useState<EventFacets | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const selectedEvent = Number(params.get("event")) || null;
  const selectedIssue = Number(params.get("issue")) || null;

  useEffect(() => setDraft(query), [query]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .briefing(query, controller.signal)
      .then((b) => {
        setBriefing(b);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to generate the briefing");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, reloadKey]);

  useEffect(() => {
    const controller = new AbortController();
    api.briefingPeriods(controller.signal).then(setPeriods).catch(() => undefined);
    api.eventFacets(controller.signal).then(setFacets).catch(() => undefined);
    return () => controller.abort();
  }, []);

  const setPanel = useCallback(
    (key: "event" | "issue", value: number | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete("event");
          next.delete("issue");
          if (value !== null) next.set(key, String(value));
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  const closePanels = useCallback(() => setPanel("event", null), [setPanel]);
  const openEvent = (pk: number) => setPanel("event", pk);
  const openIssue = (id: number) => setPanel("issue", id);

  function generate() {
    const next = new URLSearchParams();
    const mode = draft.mode ?? "shift";
    next.set("mode", mode);
    if (mode !== "custom") {
      if (draft.date) next.set("date", draft.date);
      if (mode === "shift" && draft.shift) next.set("shift", draft.shift);
    } else {
      if (draft.start) next.set("start", draft.start);
      if (draft.end) next.set("end", draft.end);
    }
    if (draft.tz) next.set("tz", draft.tz);
    if (draft.zone) next.set("zone", draft.zone);
    if (next.toString() === new URLSearchParams(Object.entries(query) as [string, string][]).toString()) setReloadKey((k) => k + 1);
    else setParams(next);
  }

  const mode = draft.mode ?? "shift";
  const zoneName = draft.tz || periods?.timezone || "UTC"; // custom bounds are entered in the selected zone, like shifts and days
  const timezones = [...new Set(["UTC", periods?.timezone ?? "UTC", "Asia/Dubai", BROWSER_TZ])];

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Operational briefing</h2>
          <p className="text-sm text-muted-foreground">
            Shift and daily summaries built from stored events, incidents and data-quality findings. Every statement links to its
            evidence.
          </p>
        </div>
        <div className="flex gap-2 print:hidden">
          <Button variant="outline" onClick={() => setReloadKey((k) => k + 1)} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} /> Regenerate
          </Button>
          <Button variant="outline" onClick={() => window.print()} disabled={!briefing}>
            <Printer /> Print
          </Button>
        </div>
      </div>

      <Card className="gap-3 py-4 print:hidden">
        <CardContent className="space-y-3 px-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex rounded-md border p-0.5" role="group" aria-label="Briefing type">
              {(["shift", "day", "custom"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setDraft((d) => ({ ...d, mode: m }))}
                  aria-pressed={mode === m}
                  className={cn("rounded px-3 py-1.5 text-sm", mode === m ? "bg-primary text-primary-foreground" : "hover:bg-muted")}
                >
                  {m === "shift" ? "Shift" : m === "day" ? "Daily" : "Custom"}
                </button>
              ))}
            </div>
            {mode !== "custom" && (
              <Field label={mode === "shift" ? "Shift start date" : "Date"}>
                <input
                  type="date"
                  className={inputClass}
                  value={draft.date ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, date: e.target.value || undefined }))}
                />
              </Field>
            )}
            {mode === "shift" && (
              <Field label="Shift">
                <select className={inputClass} value={draft.shift ?? ""} onChange={(e) => setDraft((d) => ({ ...d, shift: e.target.value || undefined }))}>
                  <option value="">Latest complete shift</option>
                  {(periods?.shifts ?? []).map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.label} (from {s.start})
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {mode === "custom" && (
              <>
                <Field label={`From (${zoneName})`}>
                  <input
                    type="datetime-local"
                    className={inputClass}
                    value={isoToZonedInput(draft.start, zoneName)}
                    onChange={(e) => setDraft((d) => ({ ...d, start: zonedInputToIso(e.target.value, zoneName) }))}
                  />
                </Field>
                <Field label={`Until, exclusive (${zoneName})`}>
                  <input
                    type="datetime-local"
                    className={inputClass}
                    value={isoToZonedInput(draft.end, zoneName)}
                    onChange={(e) => setDraft((d) => ({ ...d, end: zonedInputToIso(e.target.value, zoneName) }))}
                  />
                </Field>
              </>
            )}
            <Field label="Time zone">
              <select className={inputClass} value={draft.tz ?? ""} onChange={(e) => setDraft((d) => ({ ...d, tz: e.target.value || undefined }))}>
                <option value="">Default ({periods?.timezone ?? "UTC"})</option>
                {timezones.map((t) => (
                  <option key={t} value={t}>
                    {t}
                    {t === BROWSER_TZ ? " (this browser)" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Zone">
              <select className={inputClass} value={draft.zone ?? ""} onChange={(e) => setDraft((d) => ({ ...d, zone: e.target.value || undefined }))}>
                <option value="">All zones</option>
                {(facets?.zones ?? []).map((z) => (
                  <option key={z.value} value={z.value}>
                    {z.value}
                  </option>
                ))}
              </select>
            </Field>
            <Button onClick={generate} disabled={loading}>
              {loading && <Loader2 className="animate-spin" />} Generate briefing
            </Button>
          </div>
          {periods?.data_start && (
            <p className="text-xs text-muted-foreground">
              Stored events occurred between {formatDateTime(periods.data_start, zoneName)} and {formatDateTime(periods.data_end, zoneName)} (shown in the selected
              time zone). {periods.time_basis}
            </p>
          )}
        </CardContent>
      </Card>

      {error ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-6 text-center text-sm">
          <p className="font-medium text-destructive">Could not generate the briefing</p>
          <p className="mt-1 text-muted-foreground">{error}</p>
          <Button variant="outline" className="mt-3" onClick={() => setReloadKey((k) => k + 1)}>
            Try again
          </Button>
        </div>
      ) : !briefing ? (
        <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Generating briefing…
        </p>
      ) : (
        <div className={cn("space-y-5 transition-opacity", loading && "opacity-60")}>
          <BriefingBody b={briefing} onOpenEvent={openEvent} onOpenIssue={openIssue} />
        </div>
      )}

      {selectedEvent !== null && <EventDetailPanel eventId={selectedEvent} onClose={closePanels} />}
      {selectedIssue !== null && <QualityIssueDetailPanel issueId={selectedIssue} onClose={closePanels} />}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-36 flex-col gap-1 text-xs font-medium text-muted-foreground">
      {label}
      {children}
    </label>
  );
}

function Section({ title, summary, children, testId }: { title: string; summary?: string; children: React.ReactNode; testId?: string }) {
  return (
    <Card className="gap-3 py-4 break-inside-avoid-page" data-testid={testId}>
      <CardContent className="space-y-3 px-4">
        <div>
          <h3 className="text-base font-semibold">{title}</h3>
          {summary && <p className="text-sm text-muted-foreground">{summary}</p>}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function Metric({ label, value, tone }: { label: string; value: number | string; tone?: "warn" }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <div className={cn("text-xl font-semibold tabular-nums", tone === "warn" && "text-amber-700 dark:text-amber-300")}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

interface BodyProps {
  b: Briefing;
  onOpenEvent: (pk: number) => void;
  onOpenIssue: (id: number) => void;
}

function BriefingBody({ b, onOpenEvent, onOpenIssue }: BodyProps) {
  const m = b.key_metrics;
  const tz = b.period.timezone;
  const local = (iso: string) => formatDateTime(iso, tz);
  const exec = b.executive_summary;
  return (
    <>
      {/* 1. period + executive overview */}
      <Section title={b.period.label} testId="executive">
        <p className="text-xs text-muted-foreground" data-testid="period-line">
          {b.period.default_reason ? `${b.period.default_reason} ` : ""}Generated {formatDateTime(b.generated_at, tz)}.
        </p>
        <Collapsible label="Time basis, UTC boundaries and caveats" testId="period-details">
          <dl className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-[10rem_1fr]">
            <dt className="font-medium text-foreground">Local period</dt>
            <dd>{local(b.period.start)} – {local(b.period.end)}</dd>
            <dt className="font-medium text-foreground">UTC boundaries</dt>
            <dd className="font-mono">[{b.period.start}, {b.period.end})</dd>
            <dt className="font-medium text-foreground">Time basis</dt>
            <dd>{b.period.time_basis}</dd>
            <dt className="font-medium text-foreground">Comparisons</dt>
            <dd>{b.period.baseline}</dd>
            <dt className="font-medium text-foreground">Caveats</dt>
            <dd>Incident status is the operator status now, not at the end of the period. Events that arrive later can change these counts.</dd>
          </dl>
        </Collapsible>
        <div className="rounded-md border-l-4 border-l-slate-700 bg-muted/40 p-3 dark:border-l-slate-300">
          <h4 className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Executive overview</h4>
          <p className="text-sm leading-relaxed" data-testid="executive-summary">
            {exec.text}
          </p>
          <p className="mt-2 text-[11px] text-muted-foreground" data-testid="narrative-source">
            {exec.generated_by === "deterministic_template"
              ? "Written by deterministic templates from database values (no language model)."
              : `Worded by a local language model (${exec.provider}); every identifier and number was checked against the database.`}
            {exec.fallback_reason && ` Model output not used: ${exec.fallback_reason}.`}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          <Metric label="Events (occurrence time)" value={m.events_total} />
          <Metric label="Devices reporting" value={m.devices_reporting} />
          <Metric label="Incidents with activity" value={m.incidents_active} />
          <Metric label="Unresolved" value={m.incidents_unresolved} tone={m.incidents_unresolved ? "warn" : undefined} />
          <Metric label="New in period" value={m.incidents_new} />
          <Metric label="Need attention" value={m.incidents_immediate} tone={m.incidents_immediate ? "warn" : undefined} />
          <Metric label="Quality findings" value={m.quality_issues_total ?? 0} />
          <Metric label="Briefing confidence" value={`${b.confidence.level} (${b.confidence.score.toFixed(2)})`} />
        </div>
      </Section>

      {/* 2. priority incidents */}
      <Section
        title={`Highest-priority incidents (${b.priority_incidents.length} of ${b.unresolved_total} unresolved)`}
        summary="Ranked by a transparent score (severity, status, recency, evidence strength, conflicts, device failure, anomalies). Stored severity is never changed."
        testId="priority"
      >
        {b.priority_incidents.length === 0 ? (
          <p className="text-sm text-muted-foreground italic" data-testid="no-priority">
            No unresolved incidents had activity in this period.
          </p>
        ) : (
          <ol className="space-y-2">
            {b.priority_incidents.map((x) => (
              <PriorityItem key={x.incident_id} x={x} tz={tz} onOpenEvent={onOpenEvent} />
            ))}
          </ol>
        )}
      </Section>

      {/* 3-4. unresolved and new */}
      <div className="grid gap-5 lg:grid-cols-2">
        <IncidentList title={`Unresolved incidents (${b.unresolved_total})`} items={b.unresolved_incidents} total={b.unresolved_total}
          empty="No unresolved incidents overlap this period." note="Status is the current operator status." testId="unresolved" />
        <IncidentList title={`Newly started incidents (${b.new_total})`} items={b.new_incidents} total={b.new_total}
          empty="No incident started in this period." note="First event occurred inside the period. 'Detected late' means the engine produced it after the period ended." testId="new" />
      </div>

      {/* 5-11. sections */}
      {b.sections.map((s) => (
        <Section key={s.key} title={s.title} summary={s.summary} testId={`section-${s.key}`}>
          {s.key === "trends" && m.events_per_hour.length > 0 && <HourBars hours={m.events_per_hour} />}
          {s.findings.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">{s.empty_reason}</p>
          ) : (
            <SectionFindings findings={s.findings} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
          )}
        </Section>
      ))}

      {/* 12. recommendations */}
      <Section title="Recommended management actions" summary="Advisory only: nothing here has been performed or triggered." testId="recommendations">
        <ul className="space-y-2">
          {b.recommended_actions.map((f) => (
            <FindingCard key={f.id} finding={f} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
          ))}
        </ul>
      </Section>

      {/* 13. confidence and limitations */}
      <Section title={`Overall confidence: ${b.confidence.level} (${b.confidence.score.toFixed(2)})`}
        summary="A heuristic data-reliability score for this briefing, not a probability." testId="confidence">
        {b.confidence.factors.length > 0 && (
          <ul className="space-y-1 text-sm">
            {b.confidence.factors.map((f) => (
              <li key={f.factor} className="flex gap-2">
                <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-amber-700 dark:text-amber-300">{f.effect.toFixed(2)}</span>
                <span>{f.explanation}</span>
              </li>
            ))}
          </ul>
        )}
        <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Limitations</h4>
        <ul className="list-disc space-y-0.5 pl-5 text-sm">
          {b.confidence.limitations.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      </Section>

      {/* 14. evidence index */}
      <EvidenceIndex b={b} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
    </>
  );
}

const FINDING_PREVIEW = 4;

/** First findings visible; the rest stay rendered (collapsed on screen, always printed) so nothing is lost. */
function SectionFindings({ findings, onOpenEvent, onOpenIssue }: { findings: Briefing["sections"][number]["findings"] } & Omit<BodyProps, "b">) {
  const rest = findings.slice(FINDING_PREVIEW);
  return (
    <>
      <ul className="space-y-2">
        {findings.slice(0, FINDING_PREVIEW).map((f) => (
          <FindingCard key={f.id} finding={f} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
        ))}
      </ul>
      {rest.length > 0 && (
        <Collapsible label={`Show ${rest.length} more finding${rest.length === 1 ? "" : "s"}`} testId="more-findings">
          <ul className="space-y-2">
            {rest.map((f) => (
              <FindingCard key={f.id} finding={f} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
            ))}
          </ul>
        </Collapsible>
      )}
    </>
  );
}

function PriorityItem({ x, tz, onOpenEvent }: { x: PrioritizedIncident; tz: string; onOpenEvent: (pk: number) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={cn("rounded-md border p-3", x.immediate_attention && "border-l-4 border-l-red-600")} data-testid="priority-incident">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">{x.priority_rank}.</span>
        <SeverityBadge severity={x.severity} />
        <IncidentLink id={x.incident_id} />
        <span className="text-sm font-medium">{x.title}</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {categoryLabel(x.category)} · {x.status.replace("_", " ")} · {x.zones.join(", ") || "no zone"} · {formatDateTime(x.first_occurred_at, tz)} –{" "}
        {formatDateTime(x.last_occurred_at, tz)} · {x.event_count} events · confidence {x.confidence.toFixed(2)} ({x.confidence_level})
        {x.conflict_count > 0 && <span className="text-red-700 dark:text-red-300"> · {x.conflict_count} conflict(s)</span>}
        {x.detected_after_period && " · detected after the period ended"}
      </p>
      <p className="mt-1 text-sm">
        <span className="font-medium tabular-nums">Priority {x.priority_score.toFixed(1)}</span> — {x.reason}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-muted-foreground">Evidence:</span>
        <EventChips refs={x.evidence} onOpenEvent={onOpenEvent} />
        <button className="ml-auto inline-flex items-center gap-1 text-xs underline underline-offset-4 print:hidden" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          Why this priority <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open && (
        <table className="mt-2 w-full text-xs">
          <tbody>
            {x.factors.map((f) => (
              <tr key={f.factor} className="border-t">
                <td className="py-1 pr-2 font-medium">{f.factor.replace(/_/g, " ")}</td>
                <td className="py-1 pr-2 text-right tabular-nums">+{f.points}</td>
                <td className="py-1 text-muted-foreground">{f.explanation}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {x.recommended_actions.length > 0 && (
        <p className="mt-1.5 text-xs">
          <span className="text-muted-foreground">Recommended (advisory): </span>
          {x.recommended_actions[0]}
        </p>
      )}
    </li>
  );
}

function IncidentList({ title, items, total, empty, note, testId }: { title: string; items: PrioritizedIncident[]; total: number; empty: string; note: string; testId: string }) {
  return (
    <Section title={title} summary={note} testId={testId}>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">{empty}</p>
      ) : (
        <ul className="max-h-80 space-y-1 overflow-y-auto pr-1 text-sm print:max-h-none">
          {items.map((x) => (
            <li key={x.incident_id} className="flex items-start gap-2">
              <SeverityBadge severity={x.severity} />
              <IncidentLink id={x.incident_id} />
              <span className="min-w-0 flex-1">
                {x.title}
                <span className="text-xs text-muted-foreground"> · {x.status.replace("_", " ")}</span>
                {x.detected_after_period && <span className="text-xs text-amber-700 dark:text-amber-300"> · detected late</span>}
              </span>
            </li>
          ))}
          {total > items.length && <li className="text-xs text-muted-foreground">… and {total - items.length} more (see the Incidents page)</li>}
        </ul>
      )}
    </Section>
  );
}

function HourBars({ hours }: { hours: { hour_utc: string; count: number }[] }) {
  const max = Math.max(...hours.map((h) => h.count), 1);
  return (
    <div aria-label="Events per hour (UTC)" className="space-y-1">
      <div className="flex h-16 items-end gap-0.5">
        {hours.map((h) => (
          <div key={h.hour_utc} className="flex-1 rounded-t bg-slate-400 dark:bg-slate-500" style={{ height: `${(h.count / max) * 100}%` }}
            title={`${h.hour_utc}:00 UTC: ${h.count} events`} />
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Events per hour (UTC), {hours[0].hour_utc.slice(11)}:00 to {hours[hours.length - 1].hour_utc.slice(11)}:00 · peak {max.toLocaleString()}
      </p>
    </div>
  );
}

function EvidenceIndex({ b, onOpenEvent, onOpenIssue }: BodyProps) {
  const [open, setOpen] = useState(false);
  const g = b.grounding;
  return (
    <Section title="Supporting evidence references" testId="evidence-index">
      <p className={cn("flex items-start gap-2 text-sm", !g.all_references_verified && "text-destructive")} data-testid="grounding">
        {g.all_references_verified ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <ShieldAlert className="mt-0.5 size-4 shrink-0" />}
        {g.all_references_verified
          ? `All ${g.events_referenced} source events, ${g.incidents_referenced} incidents and ${g.quality_issues_referenced} quality findings referenced in this briefing were verified against the database.`
          : `Unverified references: ${g.unverified_references.join(", ")}`}
      </p>
      <button className="text-sm underline underline-offset-4 print:hidden" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? "Hide" : "Show"} all {b.evidence_index.length} event references
      </button>
      {(open || b.evidence_index.length === 0) && (
        <div className="flex flex-wrap gap-1.5">
          {b.evidence_index.length === 0 ? (
            <span className="text-sm text-muted-foreground italic">No source events are referenced.</span>
          ) : (
            <EventChips refs={b.evidence_index} onOpenEvent={onOpenEvent} />
          )}
        </div>
      )}
      {b.quality_issue_ids.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">Quality findings:</span>
          {b.quality_issue_ids.slice(0, 40).map((id) => (
            <button key={id} onClick={() => onOpenIssue(id)} className="rounded border px-1.5 py-0.5 hover:bg-muted">
              #{id}
            </button>
          ))}
          {b.quality_issue_ids.length > 40 && <span className="text-muted-foreground">+{b.quality_issue_ids.length - 40}</span>}
        </div>
      )}
    </Section>
  );
}
