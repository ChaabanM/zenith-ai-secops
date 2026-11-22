import { AlertTriangle, ArrowLeft, ExternalLink, Info, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import { Collapsible } from "@/components/evidence";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api, ApiError } from "@/services/api";
import type { IncidentDetail } from "@/types/incidents";

import { EventDetailPanel } from "../events/EventDetailPanel";
import { SeverityBadge } from "../events/EventsTable";
import { formatDateTime, sourceLabel } from "../events/format";
import { QualityIssueDetailPanel } from "../quality/QualityIssueDetailPanel";
import { issueTypeLabel } from "../quality/labels";
import { ConfidenceScore, StatusBadge } from "./badges";
import { ConflictComparison } from "./ConflictComparison";
import { IncidentTimeline } from "./IncidentTimeline";
import { categoryLabel, evidenceClass, linkBasis } from "./labels";
import { StatusControls } from "./StatusControls";

export function IncidentDetailPage() {
  const { id } = useParams();
  const incidentId = Number(id);
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const navState = location.state as { from?: string; backTo?: { to: string; label: string } } | null;
  const from = navState?.from ?? "";
  const back = navState?.backTo ?? { to: `/incidents${from}`, label: "Back to incidents" };
  const selectedEvent = Number(params.get("event")) || null;
  const selectedIssue = Number(params.get("issue")) || null;

  useEffect(() => {
    if (!Number.isInteger(incidentId) || incidentId <= 0) {
      setError({ message: `"${id}" is not a valid incident id.`, notFound: true });
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    api
      .getIncident(incidentId, controller.signal)
      .then((d) => {
        setIncident(d);
        setError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError({
          message: err instanceof Error ? err.message : "Failed to load incident",
          notFound: err instanceof ApiError && err.status === 404,
        });
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [id, incidentId, reloadKey]);

  const setParam = useCallback(
    (key: "event" | "issue", value: number | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete("event");
          next.delete("issue");
          if (value !== null) next.set(key, String(value));
          return next;
        },
        { replace: true, state: location.state },
      ),
    [setParams, location.state],
  );
  const closePanels = useCallback(() => setParam("event", null), [setParam]);

  const backLink = (
    <Link to={back.to} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" /> {back.label}
    </Link>
  );

  if (error) {
    return (
      <div className="mx-auto max-w-7xl space-y-4">
        {backLink}
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-6 text-center text-sm">
          <p className="font-medium text-destructive">{error.notFound ? `Incident #${id} was not found` : "Could not load the incident"}</p>
          <p className="mt-1 text-muted-foreground">{error.message}</p>
          {!error.notFound && (
            <Button variant="outline" className="mt-3" onClick={() => setReloadKey((k) => k + 1)}>
              Try again
            </Button>
          )}
        </div>
      </div>
    );
  }
  if (!incident) {
    return (
      <div className="mx-auto max-w-7xl space-y-4">
        {backLink}
        <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading incident…
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex items-center justify-between gap-3">
        {backLink}
        <Button variant="outline" size="sm" onClick={() => setReloadKey((k) => k + 1)} disabled={loading}>
          <RefreshCw className={cn(loading && "animate-spin")} /> Refresh
        </Button>
      </div>
      <IncidentBody
        incident={incident}
        selectedEvent={selectedEvent}
        onOpenEvent={(pk) => setParam("event", pk)}
        onOpenIssue={(qid) => setParam("issue", qid)}
        onUpdated={setIncident}
      />
      {selectedEvent !== null && <EventDetailPanel eventId={selectedEvent} onClose={closePanels} currentIncidentId={incident.id} />}
      {selectedIssue !== null && <QualityIssueDetailPanel issueId={selectedIssue} onClose={closePanels} />}
    </div>
  );
}

function Section({ title, description, children, className }: { title: string; description?: string; children: React.ReactNode; className?: string }) {
  return (
    <Card className={cn("gap-3 py-4", className)}>
      <CardContent className="space-y-3 px-4">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: "warn" | "bad" }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <div
        className={cn(
          "text-lg font-semibold tabular-nums",
          tone === "warn" && "text-amber-700 dark:text-amber-300",
          tone === "bad" && "text-red-700 dark:text-red-300",
        )}
      >
        {value}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

function List({ items, empty, testId, preview }: { items: string[]; empty: string; testId?: string; preview?: number }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground italic" data-testid={testId}>{empty}</p>;
  const shown = preview ? items.slice(0, preview) : items;
  const rest = preview ? items.slice(preview) : [];
  return (
    <div data-testid={testId}>
      <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed">
        {shown.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
      {rest.length > 0 && (
        <Collapsible label={`Show ${rest.length} more`} className="mt-1">
          <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed">
            {rest.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </Collapsible>
      )}
    </div>
  );
}

const ISSUE_PREVIEW = 6;

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function IncidentBody({
  incident: d,
  selectedEvent,
  onOpenEvent,
  onOpenIssue,
  onUpdated,
}: {
  incident: IncidentDetail;
  selectedEvent: number | null;
  onOpenEvent: (pk: number) => void;
  onOpenIssue: (id: number) => void;
  onUpdated: (i: IncidentDetail) => void;
}) {
  const classes = useMemo(() => {
    const c = { strong: 0, contextual: 0, conflict: 0, uncertain: 0 };
    for (const t of d.timeline) c[evidenceClass(t)] += 1;
    return c;
  }, [d.timeline]);
  const linkGroups = useMemo(() => {
    const m = new Map<string, { count: number; description: string; core: boolean }>();
    for (const t of d.timeline) {
      const g = m.get(t.link_type) ?? { count: 0, description: t.link_description, core: t.core };
      g.count += 1;
      m.set(t.link_type, g);
    }
    return [...m.entries()].sort((a, b) => Number(b[1].core) - Number(a[1].core) || b[1].count - a[1].count);
  }, [d.timeline]);
  const [showAllIssues, setShowAllIssues] = useState(false);
  const core = asNumber(d.details.core_events) ?? d.timeline.filter((t) => t.core).length;
  const contextual = asNumber(d.details.contextual_events) ?? d.timeline.filter((t) => !t.core).length;
  const duplicatesIgnored = asNumber(d.details.duplicate_submissions_ignored) ?? 0;

  return (
    <>
      {/* ------------------------------------------------------------------ header */}
      <Card className="gap-3 py-4">
        <CardContent className="space-y-3 px-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono text-sm font-semibold text-foreground" data-testid="incident-id">
              Incident #{d.id}
            </span>
            <span>·</span>
            <span>{categoryLabel(d.category)}</span>
          </div>
          <h2 className="text-xl leading-snug font-semibold tracking-tight">{d.title}</h2>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <SeverityBadge severity={d.severity} />
            <StatusBadge status={d.status} />
            <span className="text-muted-foreground">Confidence</span>
            <ConfidenceScore value={d.confidence} level={d.confidence_level} />
            <span className="text-muted-foreground">Location</span>
            <span className="font-medium" data-testid="incident-location">
              {d.zones.join(", ") || <span className="font-normal italic text-muted-foreground">none reported</span>}
            </span>
          </div>
          <p className="text-sm leading-relaxed">{d.summary}</p>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt className="text-xs text-muted-foreground">First observed</dt>
              <dd className="tabular-nums">{formatDateTime(d.first_occurred_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Last observed</dt>
              <dd className="tabular-nums">{formatDateTime(d.last_occurred_at)}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs text-muted-foreground">Devices involved</dt>
              <dd className="font-mono text-xs leading-relaxed">{d.devices.join(", ")}</dd>
            </div>
          </dl>
          <Collapsible label="Record details (detection time, last update, rule version)" testId="record-details">
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">First detected by the engine</dt>
                <dd className="tabular-nums">{formatDateTime(d.detected_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Content last updated</dt>
                <dd className="tabular-nums">{formatDateTime(d.updated_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Correlation rule version</dt>
                <dd className="font-mono text-xs">{d.rule_version}</dd>
              </div>
            </dl>
          </Collapsible>
          {d.correlation_state !== "active" && (
            <p className="flex items-start gap-2 rounded-md border border-amber-300/60 bg-amber-50 p-2 text-sm dark:bg-amber-500/10">
              <Info className="mt-0.5 size-4 shrink-0" />
              {d.correlation_state === "merged" && d.superseded_by_id ? (
                <span>
                  This incident was merged into{" "}
                  <Link className="underline" to={`/incidents/${d.superseded_by_id}`}>
                    incident #{d.superseded_by_id}
                  </Link>{" "}
                  by a later correlation run. It is kept for history.
                </span>
              ) : (
                <span>This incident is no longer produced by the correlation engine (obsolete). It is kept for history.</span>
              )}
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-5">
          {/* -------------------------------------------------------------- evidence summary */}
          <Section title="Evidence summary">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" data-testid="evidence-summary">
              <Stat label="Linked source events" value={d.timeline_total} />
              <Stat label="Strongly linked" value={core} />
              <Stat label="Contextual (supporting only)" value={contextual} />
              <Stat label="Conflicts" value={d.conflicts.length} tone={d.conflicts.length ? "bad" : undefined} />
              <Stat label="Uncertain entries" value={classes.uncertain} tone={classes.uncertain ? "warn" : undefined} />
              <Stat label="Missing-info notes" value={d.missing_information.length} tone={d.missing_information.length ? "warn" : undefined} />
            </div>
            <p className="text-sm">
              <span className="text-muted-foreground">Source categories: </span>
              {d.sources.map(sourceLabel).join(", ")}
            </p>
            <dl className="grid gap-1 rounded-md bg-muted/40 p-2 text-xs sm:grid-cols-2" data-testid="evidence-kinds">
              <div>
                <dt className="font-semibold">Contextual evidence</dt>
                <dd className="text-muted-foreground">
                  Attached because of supporting circumstances (same zone, a short time window, a compatible event type) rather than a
                  strong identifier. It is never identity evidence and may be unrelated.
                </dd>
              </div>
              <div>
                <dt className="font-semibold">Uncertain entries</dt>
                <dd className="text-muted-foreground">
                  Records the system flags as uncertain: sensor-reported confidence below {d.low_source_confidence_threshold.toFixed(2)}, or
                  output of a device during reported degradation. A contextual item is not counted here unless it is also flagged.
                </dd>
              </div>
            </dl>
            {duplicatesIgnored > 0 && (
              <p className="text-xs text-muted-foreground">
                {duplicatesIgnored} repeated submission(s) of these events were not counted as extra corroboration.
              </p>
            )}
          </Section>

          {/* -------------------------------------------------------------- relationship */}
          <Section
            title="Why these events are grouped"
            description="From the correlation engine's recorded links. Matching time and place alone never establishes identity."
          >
            <p className="text-sm leading-relaxed" data-testid="linkage-explanation">
              {d.linkage_explanation}
            </p>
            <Collapsible label={`How each link was established (${linkGroups.length} link type${linkGroups.length === 1 ? "" : "s"})`} testId="link-methodology">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Link basis</th>
                    <th className="py-1 pr-3 font-medium">Meaning</th>
                    <th className="py-1 pr-3 font-medium">Strength</th>
                    <th className="py-1 text-right font-medium">Events</th>
                  </tr>
                </thead>
                <tbody>
                  {linkGroups.map(([type, g]) => (
                    <tr key={type} className="border-t align-top">
                      <td className="py-1.5 pr-3 font-medium">{linkBasis(type)}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground">{g.description}</td>
                      <td className="py-1.5 pr-3 whitespace-nowrap">{g.core ? "Strong" : "Contextual only"}</td>
                      <td className="py-1.5 text-right tabular-nums">{g.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </Collapsible>
          </Section>

          {/* -------------------------------------------------------------- timeline */}
          <Section title={`Timeline (${d.timeline_total} events)`} description="Select an event ID to inspect the source event and its original raw submission.">
            <IncidentTimeline
              entries={d.timeline}
              total={d.timeline_total}
              threshold={d.low_source_confidence_threshold}
              qualityIssues={d.quality_issues}
              selectedEvent={selectedEvent}
              onOpenEvent={onOpenEvent}
              onOpenIssue={onOpenIssue}
            />
          </Section>

          {/* -------------------------------------------------------------- reliability */}
          <Section
            title="Evidence reliability"
            description="What sensors reported, what the system inferred from it, and what is contradictory, missing or uncertain."
          >
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Observed or reported</h4>
                <p className="text-xs text-muted-foreground">As recorded by devices and operators; a sensor identification is not by itself verified.</p>
                <List items={d.observations} empty="No observations recorded." testId="observations" preview={3} />
              </div>
              <div className="space-y-1.5">
                <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">System inferences</h4>
                <p className="text-xs text-muted-foreground">Conclusions the engine drew from the links above; not directly observed.</p>
                <List items={d.inferences} empty="No inferences beyond the links themselves." testId="inferences" preview={3} />
              </div>
            </div>
            {d.conflicts.length > 0 ? (
              <div className="space-y-2 rounded-md border border-red-300/70 bg-red-50/50 p-3 dark:bg-red-500/5" data-testid="conflicts" data-state="conflicts">
                <h4 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-red-800 uppercase dark:text-red-300">
                  <AlertTriangle className="size-3.5" aria-hidden /> Conflicting evidence ({d.conflicts.length})
                </h4>
                <List items={d.conflicts} empty="" />
                <ConflictComparison timeline={d.timeline} qualityIssues={d.quality_issues} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
              </div>
            ) : (
              <div className="space-y-1" data-testid="conflicts" data-state="none">
                <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Conflicting evidence</h4>
                <p className="text-sm text-muted-foreground">
                  No conflicts reported by the correlation engine. This does not prove that every observation is correct.
                </p>
                <ConflictComparison timeline={d.timeline} qualityIssues={d.quality_issues} onOpenEvent={onOpenEvent} onOpenIssue={onOpenIssue} />
              </div>
            )}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <h4 className="text-xs font-semibold tracking-wide text-amber-800 uppercase dark:text-amber-300">Missing information</h4>
                <List items={d.missing_information} empty="No missing information was reported." testId="missing" />
              </div>
              <div className="space-y-1.5">
                <h4 className="text-xs font-semibold tracking-wide text-amber-800 uppercase dark:text-amber-300">Uncertainty</h4>
                <List items={d.uncertainty} empty="No specific uncertainty was reported." testId="uncertainty" />
                {classes.contextual + classes.uncertain > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {classes.contextual} contextual and {classes.uncertain} uncertain timeline entr
                    {classes.contextual + classes.uncertain === 1 ? "y is" : "ies are"} weaker than the strong links and may be unrelated.
                  </p>
                )}
              </div>
            </div>
          </Section>
        </div>

        {/* ---------------------------------------------------------------- side column */}
        <div className="min-w-0 space-y-5">
          <Section title="Investigation status">
            <StatusControls incident={d} onUpdated={onUpdated} />
          </Section>

          <Section title="Recommended next actions" description="Recommendations only. Nothing below has been performed or triggered.">
            {d.recommended_actions.length ? (
              <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-relaxed" data-testid="recommendations">
                {d.recommended_actions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground italic">No recommendations were produced for this incident.</p>
            )}
          </Section>

          <Section title="Severity and confidence">
            <div className="space-y-1 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Severity</span>
                <SeverityBadge severity={d.severity} />
              </div>
              <p className="leading-relaxed" data-testid="severity-reason">
                {d.severity_reason}
              </p>
            </div>
            <div className="space-y-1 border-t pt-3 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Confidence</span>
                <ConfidenceScore value={d.confidence} level={d.confidence_level} />
              </div>
              <p className="leading-relaxed" data-testid="confidence-reason">
                {d.confidence_reason}
              </p>
              <p className="text-xs text-muted-foreground">
                Scores are heuristic rankings of evidence strength, not calibrated probabilities.
              </p>
            </div>
          </Section>

          <Section title={`Related data-quality findings (${d.quality_issues.length})`} description="Phase 3 findings on this incident's events.">
            {d.quality_issues.length === 0 ? (
              <p className="text-sm text-muted-foreground italic">No data-quality findings involve these events.</p>
            ) : (
              <ul className="space-y-2" data-testid="quality-issues">
                {d.quality_issues.slice(0, showAllIssues ? undefined : ISSUE_PREVIEW).map((q) => (
                  <li key={q.id} className="space-y-1 rounded-md border p-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={q.severity} />
                      <span className="text-xs text-muted-foreground">{issueTypeLabel(q.issue_type)}</span>
                    </div>
                    <button className="text-left font-medium underline-offset-4 hover:underline" onClick={() => onOpenIssue(q.id)}>
                      #{q.id} {q.title}
                    </button>
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span className="font-mono">{q.event_ids.join(", ")}</span>
                      <Link className="inline-flex items-center gap-1 hover:text-foreground" to={`/data-quality?issue=${q.id}`}>
                        Data Quality page <ExternalLink className="size-3" />
                      </Link>
                    </div>
                  </li>
                ))}
                {d.quality_issues.length > ISSUE_PREVIEW && (
                  <li>
                    <button className="text-sm underline underline-offset-4" onClick={() => setShowAllIssues((v) => !v)}>
                      {showAllIssues ? "Show fewer" : `Show all ${d.quality_issues.length} findings`}
                    </button>
                  </li>
                )}
              </ul>
            )}
          </Section>

          {d.merged_incident_ids.length > 0 && (
            <Section title="Merged incidents" description="Earlier incidents whose evidence now belongs to this one.">
              <ul className="flex flex-wrap gap-2 text-sm">
                {d.merged_incident_ids.map((m) => (
                  <li key={m}>
                    <Link className="font-mono underline" to={`/incidents/${m}`}>
                      #{m}
                    </Link>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </div>
    </>
  );
}
