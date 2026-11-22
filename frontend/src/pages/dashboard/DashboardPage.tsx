import { Activity, ArrowRight, CheckCircle2, Database, FileText, RefreshCw, ShieldAlert, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Collapsible } from "@/components/evidence";
import { EmptyNote, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import type { DashboardQuery, DashboardSummary } from "@/types/dashboard";
import type { EventFacets } from "@/types/events";
import { UNRESOLVED_STATUSES, type IncidentStatus } from "@/types/incidents";

import { BasisBadge, EventChips, FindingCard, IncidentLink } from "../briefing/FindingCard";
import { inputClass } from "../events/controls";
import { EventDetailPanel } from "../events/EventDetailPanel";
import { SeverityBadge } from "../events/EventsTable";
import { formatDateTime, hourLabel, sourceLabel } from "../events/format";
import { ConfidenceScore, StatusBadge } from "../incidents/badges";
import { categoryLabel } from "../incidents/labels";
import { QualityIssueDetailPanel } from "../quality/QualityIssueDetailPanel";
import { issueTypeLabel } from "../quality/labels";

const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

function readQuery(params: URLSearchParams): DashboardQuery {
  const q: DashboardQuery = {};
  const mode = params.get("mode");
  if (mode === "day" || mode === "shift") q.mode = mode;
  for (const k of ["date", "shift", "tz", "zone"] as const) {
    const v = params.get(k);
    if (v) q[k] = v;
  }
  return q;
}

export function DashboardPage() {
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => readQuery(params), [params]);
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [facets, setFacets] = useState<EventFacets | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const selectedEvent = Number(params.get("event")) || null;
  const selectedIssue = Number(params.get("issue")) || null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .dashboard(query, controller.signal)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load the overview");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, reloadKey]);

  useEffect(() => {
    const controller = new AbortController();
    api.eventFacets(controller.signal).then(setFacets).catch(() => undefined);
    return () => controller.abort();
  }, [reloadKey]);

  const update = useCallback(
    (patch: Record<string, string | undefined>) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v) next.set(k, v);
            else next.delete(k);
          }
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  const openEvent = (pk: number) => update({ event: String(pk), issue: undefined });
  const openIssue = (id: number) => update({ issue: String(id), event: undefined });
  const closePanels = useCallback(() => update({ event: undefined, issue: undefined }), [update]);

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Security operations overview</h2>
          <p className="text-sm text-muted-foreground">
            What happened in the selected period, what needs attention, and how reliable the data is. Every figure comes from stored
            records.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            Period
            <select className={cn(inputClass, "w-auto")} value={query.mode ?? "shift"} aria-label="Period"
              onChange={(e) => update({ mode: e.target.value === "shift" ? undefined : e.target.value, date: undefined, shift: undefined })}>
              <option value="shift">Latest shift with data</option>
              <option value="day">Latest day with data</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            Time zone
            <select className={cn(inputClass, "w-auto")} value={query.tz ?? ""} aria-label="Time zone" onChange={(e) => update({ tz: e.target.value || undefined })}>
              <option value="">Default (server)</option>
              {[...new Set(["UTC", "Asia/Dubai", BROWSER_TZ])].map((t) => (
                <option key={t} value={t}>
                  {t}
                  {t === BROWSER_TZ ? " (this browser)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            Zone
            <select className={cn(inputClass, "w-auto")} value={query.zone ?? ""} aria-label="Zone" onChange={(e) => update({ zone: e.target.value || undefined })}>
              <option value="">All zones</option>
              {(facets?.zones ?? []).map((z) => (
                <option key={z.value} value={z.value}>
                  {z.value}
                </option>
              ))}
            </select>
          </label>
          <Button variant="outline" onClick={() => setReloadKey((k) => k + 1)} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} /> Refresh
          </Button>
        </div>
      </div>

      {error ? (
        <ErrorState title="Could not load the overview" message={error} onRetry={() => setReloadKey((k) => k + 1)} />
      ) : !data ? (
        <LoadingState label="Loading overview…" />
      ) : data.stored_totals.events_stored === 0 ? (
        <GettingStarted />
      ) : (
        <div className={cn("space-y-5 transition-opacity", loading && "opacity-60")}>
          <Overview d={data} onOpenEvent={openEvent} onOpenIssue={openIssue} />
        </div>
      )}

      {selectedEvent !== null && <EventDetailPanel eventId={selectedEvent} onClose={closePanels} />}
      {selectedIssue !== null && <QualityIssueDetailPanel issueId={selectedIssue} onClose={closePanels} />}
    </div>
  );
}

function GettingStarted() {
  return (
    <Card className="gap-3 py-5" data-testid="getting-started">
      <CardContent className="space-y-3 px-5">
        <div className="flex items-center gap-2">
          <Database className="size-5 text-muted-foreground" />
          <h3 className="text-lg font-semibold">No events stored yet</h3>
        </div>
        <p className="text-sm text-muted-foreground">
          The database is empty, so there is nothing to summarise. This is not a sign that operations are normal. Load the demo dataset
          (500 submissions from all 15 source types, including duplicates, invalid records, delays, conflicts and device failures)
          through the normal ingestion pipeline:
        </p>
        <ol className="list-decimal space-y-1.5 pl-5 text-sm">
          <li>
            <Link to="/events?import=1" className="font-medium underline underline-offset-4">
              Events → Import events
            </Link>{" "}
            and choose <code>data/samples/demo_events.json</code>, or run <code>python3 scripts/load_demo.py</code>.
          </li>
          <li>Data-quality analysis and incident correlation run automatically in the background (a few seconds).</li>
          <li>Come back here: open a priority incident, select a timeline event to see its original record, then open the full briefing.</li>
        </ol>
        <Button asChild>
          <Link to="/events?import=1">
            <Upload /> Import events
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function Panel({ title, action, children, testId, className }: { title: string; action?: React.ReactNode; children: React.ReactNode; testId?: string; className?: string }) {
  return (
    <Card className={cn("gap-3 py-4", className)} data-testid={testId}>
      <CardContent className="space-y-3 px-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">{title}</h3>
          {action}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function MoreLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
      {children} <ArrowRight className="size-3" />
    </Link>
  );
}

function Metric({ label, value, sub, to, tone, testId }: { label: string; value: number; sub?: string; to: string; tone?: "bad" | "warn"; testId: string }) {
  const className = cn(
    "rounded-xl border bg-card p-4 shadow-sm transition-colors hover:bg-muted/50",
    tone === "bad" && value > 0 && "border-l-4 border-l-red-600",
    tone === "warn" && value > 0 && "border-l-4 border-l-amber-500",
  );
  const body = (
    <>
      <div className="text-2xl font-semibold tabular-nums">{value.toLocaleString()}</div>
      <div className="text-xs font-medium">{label}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </>
  );
  return to.startsWith("#") ? (
    <a href={to} className={className} data-testid={testId}>
      {body}
    </a>
  ) : (
    <Link to={to} className={className} data-testid={testId}>
      {body}
    </Link>
  );
}

function Bars({ items, label }: { items: { key: string; label: string; count: number; to?: string }[]; label: string }) {
  const max = Math.max(...items.map((i) => i.count), 1);
  return (
    <ul className="space-y-1" aria-label={label}>
      {items.map((i) => (
        <li key={i.key} className="grid grid-cols-[minmax(0,9rem)_1fr_3.5rem] items-center gap-2 text-xs">
          {i.to ? (
            <Link to={i.to} className="truncate hover:underline">
              {i.label}
            </Link>
          ) : (
            <span className="truncate">{i.label}</span>
          )}
          <span className="h-2 rounded-full bg-muted">
            <span className="block h-2 rounded-full bg-slate-500 dark:bg-slate-400" style={{ width: `${Math.max(2, (i.count / max) * 100)}%` }} />
          </span>
          <span className="text-right tabular-nums">{i.count.toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );
}

interface OverviewProps {
  d: DashboardSummary;
  onOpenEvent: (pk: number) => void;
  onOpenIssue: (id: number) => void;
}

function Overview({ d, onOpenEvent, onOpenIssue }: OverviewProps) {
  const p = d.period;
  const m = d.period_metrics;
  const t = d.stored_totals;
  const tz = p.timezone;
  const zone = d.filters.zones.join(",");
  const range = new URLSearchParams({ occurred_from: p.start, occurred_to: p.end, ...(zone ? { zone } : {}) });
  const unresolved = UNRESOLVED_STATUSES.join(",");
  const briefingLink = `/briefings?${new URLSearchParams({ ...p.query, ...(zone ? { zone } : {}) })}`;
  const hours = d.events_per_hour;
  const maxHour = Math.max(...hours.map((h) => h.count), 1);

  return (
    <>
      {/* period and scope */}
      <div className="rounded-md border bg-muted/30 px-4 py-3 text-sm" data-testid="period">
        <p>
          <span className="font-semibold">{p.label}</span>
          {zone && <span className="text-muted-foreground"> · zone {zone}</span>}
        </p>
        {p.default_reason && <p className="text-xs text-muted-foreground">{p.default_reason}</p>}
        <Collapsible label="Time basis, UTC boundaries and caveats" testId="period-details" className="mt-1">
          <dl className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-[10rem_1fr]">
            <dt className="font-medium text-foreground">Local period</dt>
            <dd>{formatDateTime(p.start, tz)} – {formatDateTime(p.end, tz)}</dd>
            <dt className="font-medium text-foreground">UTC boundaries</dt>
            <dd className="font-mono">[{p.start}, {p.end})</dd>
            <dt className="font-medium text-foreground">What is counted</dt>
            <dd>{d.scope.period}</dd>
            <dt className="font-medium text-foreground">Caveats</dt>
            <dd>{d.scope.status} Events that arrive later can change these counts.</dd>
          </dl>
        </Collapsible>
        {m.period_extends_past_stored_data && (
          <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
            The period extends beyond the latest stored event ({formatDateTime(m.latest_stored_event_at, tz)}); later data may be missing.
          </p>
        )}
      </div>

      {/* A. operational summary */}
      <section aria-label="Operational summary" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Metric testId="m-events" label="Events in period" value={m.events_total} sub={`${m.devices_reporting} devices · ${m.zones_reporting} zones`} to={`/events?${range}`} />
        <Metric testId="m-incidents" label="Incidents with activity" value={m.incidents_active} sub={`${m.incidents_new} started in period`} to={`/incidents?${range}`} />
        <Metric testId="m-unresolved" label="Unresolved incidents" value={m.incidents_unresolved} sub="status now" tone="warn" to={`/incidents?${range}&status=${unresolved}`} />
        <Metric testId="m-high" label="High / critical incidents" value={m.incidents_high_critical} sub={`${m.incidents_unresolved_high_critical} unresolved`} tone="bad"
          to={`/incidents?${range}&severity=critical,high`} />
        <Metric testId="m-quality" label="Data-quality findings" value={m.quality_issues_total} sub={`${d.quality_high_severity_total} high or critical`} to={`/data-quality?${range}`} />
        <Metric testId="m-health" label="Device-health concerns" value={m.device_health_concerns}
          sub={`${m.devices_with_health_reports} with health reports`} tone="warn" to="#device-health" />
      </section>
      <p className="text-xs text-muted-foreground" data-testid="stored-totals">
        <span className="font-medium text-foreground">All stored data</span> (ignores period and zone): {t.events_stored.toLocaleString()} events
        from {formatDateTime(t.data_start, tz)} to {formatDateTime(t.data_end, tz)} · {t.incidents_active.toLocaleString()} incidents,{" "}
        {t.incidents_unresolved.toLocaleString()} unresolved ({t.incidents_unresolved_high_critical.toLocaleString()} high/critical) ·{" "}
        {t.quality_issues.toLocaleString()} quality findings · {t.submissions_invalid.toLocaleString()} invalid and{" "}
        {t.submissions_duplicate.toLocaleString()} duplicate submissions.
      </p>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-5">
          {/* B. priority incidents */}
          <Panel title="Highest-priority unresolved incidents" testId="priority" action={<MoreLink to={`/incidents?${range}&status=${unresolved}`}>All unresolved</MoreLink>}>
            {d.priority_incidents.length === 0 ? (
              <EmptyNote testId="no-priority">No unresolved incidents had activity in this period. This reflects stored events only; it does not confirm the area was safe.</EmptyNote>
            ) : (
              <ol className="divide-y rounded-md border">
                {d.priority_incidents.map((x) => (
                  <li key={x.incident_id} className="space-y-1 p-3" data-testid="priority-incident">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={x.severity} />
                      <StatusBadge status={x.status as IncidentStatus} />
                      <IncidentLink id={x.incident_id} label="Back to overview" />
                      <span className="text-sm font-medium">{x.title}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span>{categoryLabel(x.category)}</span>
                      <span>{x.zones.join(", ") || "no zone"}</span>
                      <span>last event {formatDateTime(x.last_occurred_at, tz)}</span>
                      <ConfidenceScore value={x.confidence} level={x.confidence_level} compact />
                      <span title="Transparent additive ranking score, not a probability">priority {x.priority_score.toFixed(1)}</span>
                      {x.conflict_count > 0 && <span className="text-red-700 dark:text-red-300">{x.conflict_count} conflict(s)</span>}
                    </div>
                    <p className="text-xs">{x.reason}</p>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="text-muted-foreground">Evidence:</span>
                      <EventChips refs={x.evidence} onOpenEvent={onOpenEvent} />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Panel>

          {/* C. event activity */}
          <Panel title="Event activity" testId="activity" action={<MoreLink to={`/events?${range}`}>Events in period</MoreLink>}>
            {m.events_total === 0 ? (
              <EmptyNote>No events with an occurrence time in this period are stored. That is not confirmation of normal operations.</EmptyNote>
            ) : (
              <div className="grid gap-5 md:grid-cols-2">
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">By source (sensor-reported events)</h4>
                  <Bars label="Events by source"
                    items={d.events_by_source.map((s) => ({ key: s.value, label: sourceLabel(s.value), count: s.count,
                      to: `/events?${range}&source_type=${s.value}` }))} />
                </div>
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Volume per hour ({tz})</h4>
                  <div className="flex h-24 items-end gap-0.5" aria-label="Events per hour">
                    {hours.map((h) => (
                      <div key={h.hour_utc} className="flex-1 rounded-t bg-slate-400 dark:bg-slate-500"
                        style={{ height: `${(h.count / maxHour) * 100}%` }} title={`${hourLabel(h.hour_utc, tz)}: ${h.count} events`} />
                    ))}
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {hourLabel(hours[0].hour_utc, tz)} – {hourLabel(hours[hours.length - 1].hour_utc, tz)} · peak {maxHour.toLocaleString()} events per hour
                  </p>
                </div>
              </div>
            )}
            <div className="space-y-1.5">
              <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Spikes reported by the data-quality engine</h4>
              {d.spikes.length === 0 ? (
                <EmptyNote>No spikes were reported in this period.</EmptyNote>
              ) : (
                <ul className="space-y-1.5">
                  {d.spikes.map((f) => (
                    <li key={f.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <BasisBadge basis={f.basis} />
                      <span className="min-w-0 flex-1">{f.statement}</span>
                      {f.quality_issue_ids.map((q) => (
                        <button key={q} onClick={() => onOpenIssue(q)} className="rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted">
                          Quality #{q}
                        </button>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Panel>

          {/* E. briefing preview */}
          <Panel title="Operational briefing preview" testId="briefing-preview" action={<MoreLink to={briefingLink}>Open full briefing</MoreLink>}>
            <p className="text-sm leading-relaxed" data-testid="preview-summary">{d.briefing.executive_summary.text}</p>
            <p className="text-[11px] text-muted-foreground">
              {d.briefing.executive_summary.generated_by === "deterministic_template"
                ? "Written by deterministic templates from database values (no language model)."
                : `Worded by a local language model (${d.briefing.executive_summary.provider}), checked against the database.`}{" "}
              Briefing confidence: {d.briefing.confidence.level} ({d.briefing.confidence.score.toFixed(2)}, heuristic).
            </p>
            <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              Top recommendations (advisory, {d.briefing.recommendations.length} of {d.briefing.recommendations_total})
            </h4>
            <ul className="space-y-2">
              {d.briefing.recommendations.map((f) => (
                <FindingCard key={f.id} finding={f} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
              ))}
            </ul>
          </Panel>
        </div>

        <div className="min-w-0 space-y-5">
          {/* D. data quality */}
          <Panel title="Data quality" testId="quality" action={<MoreLink to={`/data-quality?${range}`}>Quality dashboard</MoreLink>}>
            {d.quality_by_type.length === 0 ? (
              <EmptyNote>No data-quality findings overlap this period.</EmptyNote>
            ) : (
              <Bars label="Quality findings by type"
                items={d.quality_by_type.slice(0, 8).map((q) => ({ key: q.value, label: issueTypeLabel(q.value), count: q.count,
                  to: `/data-quality?${range}&issue_type=${q.value}` }))} />
            )}
            <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              High / critical quality findings ({d.quality_high_severity_total})
            </h4>
            {d.quality_high_severity.length === 0 ? (
              <EmptyNote>None in this period.</EmptyNote>
            ) : (
              <ul className="space-y-1.5" data-testid="quality-high">
                {d.quality_high_severity.map((q) => (
                  <li key={q.id} className="flex items-start gap-2 text-sm">
                    <SeverityBadge severity={q.severity} />
                    <button className="min-w-0 text-left underline-offset-4 hover:underline" onClick={() => onOpenIssue(q.id)}>
                      #{q.id} {q.title}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Reliability indicators</h4>
            <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm" data-testid="uncertainty">
              <dt className="text-muted-foreground">Low-confidence detections (sensor-reported)</dt>
              <dd className="text-right tabular-nums">{d.uncertainty.low_confidence_detections.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Events that cannot be reliably linked</dt>
              <dd className="text-right tabular-nums">{d.uncertainty.unlinkable_events.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Delayed events</dt>
              <dd className="text-right tabular-nums">{d.uncertainty.delayed_events.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Conflicting record sets</dt>
              <dd className="text-right tabular-nums">{d.uncertainty.conflicting_record_sets.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Incidents with conflicting evidence</dt>
              <dd className="text-right tabular-nums">{d.uncertainty.incidents_with_conflicts.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Repeated submissions (counted once)</dt>
              <dd className="text-right tabular-nums">{m.duplicate_submissions_ignored.toLocaleString()}</dd>
            </dl>
          </Panel>

          {/* device health */}
          <Panel title="Device health" testId="device-health">
            <div id="device-health" className="scroll-mt-20" />
            <p className="text-xs text-muted-foreground">{d.device_health_note}</p>
            {d.device_health.length === 0 ? (
              <EmptyNote testId="no-health">No device-health problems were reported in this period, and no device fell silent against its usual volume.</EmptyNote>
            ) : (
              <ul className="space-y-2">
                {d.device_health.map((f) => (
                  <li key={f.id} className="space-y-1 rounded-md border p-2 text-sm" data-testid="health-finding">
                    <div className="flex items-start gap-2">
                      {f.basis === "missing" ? <ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-600" /> : <Activity className="mt-0.5 size-4 shrink-0 text-slate-500" />}
                      <span className="min-w-0">{f.statement}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 pl-6 text-xs">
                      <BasisBadge basis={f.basis} />
                      {f.quality_issue_ids.slice(0, 4).map((q) => (
                        <button key={q} onClick={() => onOpenIssue(q)} className="rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted">
                          Quality #{q}
                        </button>
                      ))}
                      {f.incident_ids.map((i) => (
                        <IncidentLink key={i} id={i} label="Back to overview" />
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Evidence and grounding">
            <p className={cn("flex items-start gap-2 text-sm", !d.grounding.all_references_verified && "text-destructive")} data-testid="grounding">
              {d.grounding.all_references_verified ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <ShieldAlert className="mt-0.5 size-4 shrink-0" />}
              {d.grounding.all_references_verified
                ? `All ${d.grounding.events_referenced} events, ${d.grounding.incidents_referenced} incidents and ${d.grounding.quality_issues_referenced} quality findings behind this overview were verified against the database.`
                : `Unverified references: ${d.grounding.unverified_references.join(", ")}`}
            </p>
            <p className="text-xs text-muted-foreground">{d.scope.scores}</p>
            <MoreLink to={briefingLink}>
              <FileText className="size-3" /> Full briefing with every finding and reference
            </MoreLink>
          </Panel>
        </div>
      </div>
    </>
  );
}
