import { ChevronLeft, ChevronRight, Loader2, Play, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import { SEVERITIES, type EventFacets, type Page } from "@/types/events";
import {
  INCIDENT_CATEGORIES,
  INCIDENT_STATUSES,
  UNRESOLVED_STATUSES,
  type IncidentQuery,
  type IncidentSort,
  type IncidentsSummary,
  type IncidentSummary,
} from "@/types/incidents";

import { inputClass } from "../events/controls";
import { SeverityBadge } from "../events/EventsTable";
import { formatCompactDateTime, formatDateTime, humanize, isoToLocalInput, localInputToIso, sourceLabel } from "../events/format";
import { ConfidenceScore, StatusBadge } from "./badges";
import { categoryLabel, STATUS_LABELS } from "./labels";

export const INCIDENT_FILTER_KEYS = [
  "severity",
  "status",
  "category",
  "source_type",
  "zone",
  "device_id",
  "occurred_from",
  "occurred_to",
] as const;
const PAGE_SIZES = [25, 50, 100];
export const INCIDENT_SORTS: { value: IncidentSort; label: string }[] = [
  { value: "priority", label: "Priority (unresolved, most severe first)" },
  { value: "-severity", label: "Most severe first" },
  { value: "-last_occurred_at", label: "Most recent activity" },
  { value: "first_occurred_at", label: "Oldest first" },
  { value: "-confidence", label: "Highest confidence" },
];
const UNRESOLVED = UNRESOLVED_STATUSES.join(",");
const SOURCE_TYPES = [
  "cctv", "anpr", "face_recognition", "mobile_phone_detection", "seatbelt_detection", "traffic_violation", "radar",
  "access_control", "perimeter_intrusion", "device_health", "operator_report", "emergency_alarm", "gps_fleet",
  "network_security", "weather_environmental",
];

export function readIncidentQuery(params: URLSearchParams): IncidentQuery {
  const size = Number(params.get("page_size"));
  const sort = params.get("sort") as IncidentSort | null;
  const query: IncidentQuery = {
    page: Math.max(1, Number(params.get("page")) || 1),
    page_size: PAGE_SIZES.includes(size) ? size : 25,
    sort: sort && INCIDENT_SORTS.some((s) => s.value === sort) ? sort : "priority",
  };
  for (const key of INCIDENT_FILTER_KEYS) {
    const value = params.get(key);
    if (value) query[key] = value;
  }
  return query;
}

export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const query = useMemo(() => readIncidentQuery(params), [params]);
  const [data, setData] = useState<Page<IncidentSummary> | null>(null);
  const [summary, setSummary] = useState<IncidentsSummary | null>(null);
  const [facets, setFacets] = useState<EventFacets | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [running, setRunning] = useState(false);
  const [runMessage, setRunMessage] = useState<string | null>(null);

  const update = useCallback(
    (patch: Record<string, string | number | undefined>, resetPage = true) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            if (value === undefined || value === "") next.delete(key);
            else next.set(key, String(value));
          }
          if (resetPage && !("page" in patch)) next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .listIncidents(query, controller.signal)
      .then((page) => {
        setData(page);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load incidents");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, reloadKey]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .incidentsSummary(controller.signal)
      .then((s) => {
        setSummary(s);
        setSummaryError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setSummaryError(err instanceof Error ? err.message : "Failed to load summary");
      });
    api.eventFacets(controller.signal).then(setFacets).catch(() => undefined); // suggestions only
    return () => controller.abort();
  }, [reloadKey]);

  async function runCorrelation() {
    setRunning(true);
    setRunMessage(null);
    try {
      const run = await api.correlate();
      setRunMessage(
        `Correlation ${run.status}: ${run.incidents_active.toLocaleString()} active incidents (${run.created} new, ${run.updated} updated, ${run.unchanged} unchanged).`,
      );
    } catch (err) {
      setRunMessage(`Correlation failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setRunning(false);
      setReloadKey((k) => k + 1);
    }
  }

  const hasFilters = INCIDENT_FILTER_KEYS.some((k) => query[k]);
  const resetFilters = () => update(Object.fromEntries(INCIDENT_FILTER_KEYS.map((k) => [k, undefined])));
  const open = (id: number) => navigate(`/incidents/${id}`, { state: { from: location.search } });
  const count = (list: { value: string; count: number }[] | undefined, value: string) =>
    list?.find((x) => x.value === value)?.count ?? 0;
  const lastRun = summary?.last_run;

  const cards: { label: string; value: number | undefined; hint?: string; patch: Record<string, string | undefined>; active: boolean }[] = [
    {
      label: "Total incidents",
      value: summary?.total,
      hint: summary ? `${summary.unresolved.toLocaleString()} unresolved` : undefined,
      patch: { severity: undefined, status: undefined },
      active: !query.severity && !query.status,
    },
    {
      label: "New / open",
      value: count(summary?.by_status, "open"),
      hint: summary ? `${count(summary.by_status, "acknowledged").toLocaleString()} acknowledged` : undefined,
      patch: { status: "open", severity: undefined },
      active: query.status === "open" && !query.severity,
    },
    {
      label: "Critical",
      value: count(summary?.by_severity, "critical"),
      hint: summary ? `${count(summary.unresolved_by_severity, "critical").toLocaleString()} unresolved` : undefined,
      patch: { severity: "critical", status: undefined },
      active: query.severity === "critical",
    },
    {
      label: "High severity",
      value: count(summary?.by_severity, "high"),
      hint: summary ? `${count(summary.unresolved_by_severity, "high").toLocaleString()} unresolved` : undefined,
      patch: { severity: "high", status: undefined },
      active: query.severity === "high",
    },
    {
      label: "Investigating",
      value: count(summary?.by_status, "investigating"),
      patch: { status: "investigating", severity: undefined },
      active: query.status === "investigating" && !query.severity,
    },
    {
      label: "Resolved",
      value: count(summary?.by_status, "resolved"),
      hint: summary ? `${count(summary.by_status, "false_positive").toLocaleString()} false positive` : undefined,
      patch: { status: "resolved", severity: undefined },
      active: query.status === "resolved" && !query.severity,
    },
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Incidents</h2>
          <p className="text-sm text-muted-foreground">
            Groups of related source events built by the correlation engine, with the evidence for each link. Unresolved
            high and critical incidents are listed first.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setReloadKey((k) => k + 1)} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} /> Refresh
          </Button>
          <Button onClick={runCorrelation} disabled={running}>
            {running ? <Loader2 className="animate-spin" /> : <Play />} Run correlation
          </Button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground" aria-live="polite">
        {runMessage ??
          (lastRun ? (
            <>
              Last correlation {formatDateTime(lastRun.finished_at ?? lastRun.started_at)} · {lastRun.status} ·{" "}
              {lastRun.observations_loaded.toLocaleString()} candidate events in {((lastRun.duration_ms ?? 0) / 1000).toFixed(1)} s
              {lastRun.error && <span className="text-destructive"> · {lastRun.error}</span>}
            </>
          ) : summary ? (
            "No correlation has run yet. It runs automatically after each import and data-quality analysis."
          ) : (
            " "
          ))}
      </p>

      {summaryError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load incident counts: {summaryError}
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" aria-label="Incident counts">
        {cards.map((card) => (
          <button
            key={card.label}
            className={cn(
              "rounded-xl border bg-card p-4 text-left shadow-sm transition-colors hover:bg-muted/50",
              card.active && "ring-2 ring-ring/50",
              card.label === "Critical" && "border-l-4 border-l-red-600",
              card.label === "High severity" && "border-l-4 border-l-orange-500",
            )}
            onClick={() => update(card.patch)}
            aria-pressed={card.active}
            data-testid={`card-${card.label}`}
          >
            <div className="text-2xl font-semibold tabular-nums">{summary ? (card.value ?? 0).toLocaleString() : "–"}</div>
            <div className="text-xs font-medium">{card.label}</div>
            <div className="mt-0.5 min-h-4 text-xs text-muted-foreground">{card.hint ?? ""}</div>
          </button>
        ))}
      </div>

      <Card className="gap-4 py-4">
        <CardContent className="space-y-4 px-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
            <Field label="Severity">
              <select className={inputClass} value={query.severity ?? ""} onChange={(e) => update({ severity: e.target.value || undefined })}>
                <option value="">All severities</option>
                <option value="critical,high">Critical + high</option>
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {humanize(s)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Status">
              <select className={inputClass} value={query.status ?? ""} onChange={(e) => update({ status: e.target.value || undefined })}>
                <option value="">All statuses</option>
                <option value={UNRESOLVED}>Unresolved</option>
                {INCIDENT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Category">
              <select className={inputClass} value={query.category ?? ""} onChange={(e) => update({ category: e.target.value || undefined })}>
                <option value="">All categories</option>
                {INCIDENT_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {categoryLabel(c)}
                    {summary ? ` (${count(summary.by_category, c)})` : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Source type">
              <select className={inputClass} value={query.source_type ?? ""} onChange={(e) => update({ source_type: e.target.value || undefined })}>
                <option value="">All sources</option>
                {SOURCE_TYPES.map((s) => (
                  <option key={s} value={s}>
                    {sourceLabel(s)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Zone / location">
              <select className={inputClass} value={query.zone ?? ""} onChange={(e) => update({ zone: e.target.value || undefined })}>
                <option value="">All zones</option>
                {query.zone && !facets?.zones.some((z) => z.value === query.zone) && <option value={query.zone}>{query.zone}</option>}
                {(facets?.zones ?? []).map((z) => (
                  <option key={z.value} value={z.value}>
                    {z.value}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Device ID">
              <input
                className={inputClass}
                list="incident-devices"
                placeholder="Any device"
                defaultValue={query.device_id ?? ""}
                key={query.device_id ?? ""}
                onBlur={(e) => update({ device_id: e.target.value.trim() || undefined })}
                onKeyDown={(e) => e.key === "Enter" && update({ device_id: e.currentTarget.value.trim() || undefined })}
              />
              <datalist id="incident-devices">
                {(facets?.devices ?? []).map((d) => (
                  <option key={d.value} value={d.value} />
                ))}
              </datalist>
            </Field>
            <Field label="Active from">
              <input
                type="datetime-local"
                className={inputClass}
                value={isoToLocalInput(query.occurred_from)}
                onChange={(e) => update({ occurred_from: localInputToIso(e.target.value) })}
              />
            </Field>
            <Field label="Started before">
              <input
                type="datetime-local"
                className={inputClass}
                value={isoToLocalInput(query.occurred_to)}
                onChange={(e) => update({ occurred_to: localInputToIso(e.target.value) })}
              />
            </Field>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span className="text-muted-foreground" aria-live="polite" data-testid="result-count">
              {data ? (
                <>
                  <span className="font-medium text-foreground">{data.total.toLocaleString()}</span>{" "}
                  {hasFilters ? "matching incidents" : "incidents"}
                  {summary && hasFilters && ` of ${summary.total.toLocaleString()}`}
                </>
              ) : (
                " "
              )}
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="sm" onClick={resetFilters} disabled={!hasFilters}>
                <RotateCcw /> Clear filters
              </Button>
              <select className={cn(inputClass, "w-auto")} value={query.sort} onChange={(e) => update({ sort: e.target.value })} aria-label="Sort order">
                {INCIDENT_SORTS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
              <select
                className={cn(inputClass, "w-auto")}
                value={query.page_size}
                onChange={(e) => update({ page_size: e.target.value })}
                aria-label="Page size"
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n} per page
                  </option>
                ))}
              </select>
            </div>
          </div>

          {error ? (
            <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-6 text-center text-sm">
              <p className="font-medium text-destructive">Could not load incidents</p>
              <p className="mt-1 text-muted-foreground">{error}</p>
              <Button variant="outline" className="mt-3" onClick={() => setReloadKey((k) => k + 1)}>
                Try again
              </Button>
            </div>
          ) : !data ? (
            <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading incidents…
            </p>
          ) : data.items.length === 0 ? (
            <div className="rounded-md border border-dashed py-14 text-center">
              <p className="font-medium">{hasFilters ? "No incidents match these filters" : "No incidents yet"}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {hasFilters
                  ? "Try widening the time range or clearing a filter."
                  : "Import events on the Events page. Quality analysis and correlation then run automatically, or use Run correlation."}
              </p>
              {hasFilters && (
                <Button variant="outline" className="mt-4" onClick={resetFilters}>
                  Clear filters
                </Button>
              )}
            </div>
          ) : (
            <div className={cn("overflow-x-auto rounded-md border transition-opacity", loading && "opacity-60")}>
              <IncidentTable items={data.items} from={location.search} onOpen={open} />
            </div>
          )}

          {data && data.total > 0 && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                Page {data.page.toLocaleString()} of {Math.max(1, data.total_pages).toLocaleString()}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={query.page <= 1 || loading} onClick={() => update({ page: query.page - 1 }, false)}>
                  <ChevronLeft /> Previous
                </Button>
                <Button variant="outline" size="sm" disabled={!data.has_next || loading} onClick={() => update({ page: query.page + 1 }, false)}>
                  Next <ChevronRight />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function IncidentTable({ items, from, onOpen }: { items: IncidentSummary[]; from: string; onOpen: (id: number) => void }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="bg-muted/60 text-xs text-muted-foreground">
        <tr>
          <th className="px-3 py-2 font-medium">ID</th>
          <th className="px-3 py-2 font-medium">Incident</th>
          <th className="px-3 py-2 font-medium">Severity</th>
          <th className="px-3 py-2 font-medium">Confidence</th>
          <th className="px-3 py-2 font-medium">Status</th>
          <th className="px-3 py-2 font-medium">Zone</th>
          <th className="px-3 py-2 font-medium">First observed</th>
          <th className="px-3 py-2 font-medium">Last observed</th>
          <th className="px-3 py-2 text-right font-medium">Events</th>
          <th className="px-3 py-2 font-medium">Updated</th>
        </tr>
      </thead>
      <tbody>
        {items.map((i) => {
          const unresolved = UNRESOLVED_STATUSES.includes(i.status);
          return (
            <tr
              key={i.id}
              onClick={() => onOpen(i.id)}
              className={cn(
                "cursor-pointer border-t align-top hover:bg-muted/50",
                !unresolved && "text-muted-foreground",
                unresolved && i.severity === "critical" && "bg-red-50/60 dark:bg-red-500/5",
              )}
              data-testid="incident-row"
            >
              <td
                className={cn(
                  "border-l-4 px-3 py-2 font-mono text-xs whitespace-nowrap",
                  unresolved && i.severity === "critical" ? "border-l-red-600" : unresolved && i.severity === "high" ? "border-l-orange-500" : "border-l-transparent",
                )}
              >
                #{i.id}
              </td>
              <td className="min-w-72 px-3 py-2">
                <Link
                  to={`/incidents/${i.id}`}
                  state={{ from }}
                  onClick={(e) => e.stopPropagation()}
                  className="font-medium text-foreground underline-offset-4 hover:underline"
                >
                  {i.title}
                </Link>
                <div className="text-xs text-muted-foreground">
                  {categoryLabel(i.category)}
                  {i.conflict_count > 0 && (
                    <span className="ml-2 text-red-700 dark:text-red-300">
                      {i.conflict_count} conflict{i.conflict_count > 1 ? "s" : ""}
                    </span>
                  )}
                </div>
              </td>
              <td className="px-3 py-2">
                <SeverityBadge severity={i.severity} />
              </td>
              <td className="px-3 py-2">
                <ConfidenceScore value={i.confidence} level={i.confidence_level} compact />
              </td>
              <td className="px-3 py-2">
                <StatusBadge status={i.status} />
              </td>
              <td className="px-3 py-2 text-xs whitespace-nowrap">
                {i.zones.slice(0, 2).join(", ") || <span className="italic">none</span>}
                {i.zones.length > 2 && ` +${i.zones.length - 2}`}
              </td>
              <td className="px-3 py-2 text-xs whitespace-nowrap tabular-nums">{formatCompactDateTime(i.first_occurred_at)}</td>
              <td className="px-3 py-2 text-xs whitespace-nowrap tabular-nums">{formatCompactDateTime(i.last_occurred_at)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{i.event_count}</td>
              <td className="px-3 py-2 text-xs whitespace-nowrap tabular-nums">{formatCompactDateTime(i.updated_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-medium text-muted-foreground">
      {label}
      {children}
    </label>
  );
}
