import { ChevronLeft, ChevronRight, Loader2, Play, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import { SEVERITIES, type Page, type Severity } from "@/types/events";
import type { IssueQuery, IssueSort, QualityIssueSummary, QualitySummary } from "@/types/quality";

import { inputClass } from "../events/controls";
import { SeverityBadge } from "../events/EventsTable";
import { formatDateTime, humanize, isoToLocalInput, localInputToIso, SEVERITY_STYLES, sourceLabel } from "../events/format";
import { ISSUE_TYPE_HINTS, issueTypeLabel } from "./labels";
import { QualityIssueDetailPanel } from "./QualityIssueDetailPanel";

const FILTER_KEYS = ["issue_type", "severity", "source_type", "device_id", "zone", "occurred_from", "occurred_to"] as const;
const SORTS: { value: IssueSort; label: string }[] = [
  { value: "-severity", label: "Most severe first" },
  { value: "-occurred_at", label: "Most recent first" },
  { value: "occurred_at", label: "Oldest first" },
];

function readQuery(params: URLSearchParams): IssueQuery {
  const sort = params.get("sort") as IssueSort | null;
  const query: IssueQuery = {
    page: Math.max(1, Number(params.get("page")) || 1),
    page_size: 50,
    sort: sort && SORTS.some((s) => s.value === sort) ? sort : "-severity",
  };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value) query[key] = value;
  }
  return query;
}

export function DataQualityPage() {
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => readQuery(params), [params]);
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [data, setData] = useState<Page<QualityIssueSummary> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [running, setRunning] = useState(false);
  const selected = Number(params.get("issue")) || null;

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
      .listQualityIssues(query, controller.signal)
      .then((page) => {
        setData(page);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load issues");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, reloadKey]);

  useEffect(() => {
    const controller = new AbortController();
    api.qualitySummary(controller.signal).then(setSummary).catch(() => undefined);
    return () => controller.abort();
  }, [reloadKey]);

  // While a background analysis (triggered by an ingest) is running, refresh when it finishes.
  useEffect(() => {
    if (!summary?.analysis_pending) return;
    const timer = window.setTimeout(() => setReloadKey((k) => k + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [summary]);

  async function runAnalysis() {
    setRunning(true);
    try {
      await api.analyzeQuality();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setRunning(false);
      setReloadKey((k) => k + 1);
    }
  }

  const hasFilters = FILTER_KEYS.some((k) => query[k]);
  const resetFilters = () => update(Object.fromEntries(FILTER_KEYS.map((k) => [k, undefined])));
  const closeDetail = useCallback(() => update({ issue: undefined }, false), [update]);
  const severityCounts = Object.fromEntries((summary?.by_severity ?? []).map((s) => [s.value, s.count]));
  const lastRun = summary?.last_run;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Data Quality</h2>
          <p className="text-sm text-muted-foreground">
            Unreliable, contradictory, incomplete or unusual data, with the reason each item was flagged. A quality issue is not
            by itself a security incident.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setReloadKey((k) => k + 1)} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} /> Refresh
          </Button>
          <Button onClick={runAnalysis} disabled={running}>
            {running ? <Loader2 className="animate-spin" /> : <Play />} Run analysis
          </Button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground" aria-live="polite">
        {summary?.analysis_pending || running ? (
          <span className="inline-flex items-center gap-1.5">
            <Loader2 className="size-3 animate-spin" /> Analysing newly ingested data…
          </span>
        ) : lastRun ? (
          <>
            Last analysis {formatDateTime(lastRun.finished_at ?? lastRun.started_at)} · {lastRun.status} ·{" "}
            {lastRun.events_analyzed.toLocaleString()} events and {lastRun.submissions_analyzed.toLocaleString()} submissions in{" "}
            {((lastRun.duration_ms ?? 0) / 1000).toFixed(1)} s
            {lastRun.error && <span className="text-destructive"> · {lastRun.error}</span>}
          </>
        ) : (
          "No analysis has run yet. It runs automatically after each import, or use Run analysis."
        )}
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <button
          className={cn("rounded-xl border bg-card p-4 text-left shadow-sm", !query.severity && "ring-2 ring-ring/40")}
          onClick={() => update({ severity: undefined })}
        >
          <div className="text-2xl font-semibold tabular-nums">{(summary?.total ?? 0).toLocaleString()}</div>
          <div className="text-xs text-muted-foreground">Open quality issues</div>
        </button>
        {SEVERITIES.map((sev) => (
          <button
            key={sev}
            className={cn("rounded-xl border bg-card p-4 text-left shadow-sm transition-colors hover:bg-muted/50", query.severity === sev && "ring-2 ring-ring/40")}
            onClick={() => update({ severity: query.severity === sev ? undefined : sev })}
            aria-pressed={query.severity === sev}
          >
            <div className="text-2xl font-semibold tabular-nums">{(severityCounts[sev] ?? 0).toLocaleString()}</div>
            <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={cn("inline-block size-2 rounded-full", SEVERITY_STYLES[sev as Severity].split(" ")[0])} />
              {humanize(sev)}
            </div>
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <Card className="h-fit gap-3 py-4">
          <CardContent className="space-y-1 px-3">
            <h3 className="px-1 pb-1 text-sm font-semibold">Issues by type</h3>
            {(summary?.by_type ?? []).length === 0 && <p className="px-1 text-sm text-muted-foreground">None</p>}
            {(summary?.by_type ?? []).map((t) => {
              const max = summary?.by_type[0]?.count ?? 1;
              const active = query.issue_type === t.value;
              return (
                <button
                  key={t.value}
                  title={ISSUE_TYPE_HINTS[t.value]}
                  onClick={() => update({ issue_type: active ? undefined : t.value })}
                  aria-pressed={active}
                  className={cn("w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/60", active && "bg-muted")}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate">{issueTypeLabel(t.value)}</span>
                    <span className="tabular-nums text-muted-foreground">{t.count.toLocaleString()}</span>
                  </div>
                  <div className="mt-1 h-1 rounded-full bg-muted">
                    <div className="h-1 rounded-full bg-foreground/40" style={{ width: `${Math.max(2, (t.count / max) * 100)}%` }} />
                  </div>
                </button>
              );
            })}
          </CardContent>
        </Card>

        <Card className="min-w-0 gap-4 py-4">
          <CardContent className="space-y-4 px-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Field label="Issue type">
                <select className={inputClass} value={query.issue_type ?? ""} onChange={(e) => update({ issue_type: e.target.value || undefined })}>
                  <option value="">All types</option>
                  {(summary?.by_type ?? []).map((t) => (
                    <option key={t.value} value={t.value}>
                      {issueTypeLabel(t.value)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Source">
                <select className={inputClass} value={query.source_type ?? ""} onChange={(e) => update({ source_type: e.target.value || undefined })}>
                  <option value="">All sources</option>
                  {(summary?.sources ?? []).map((s) => (
                    <option key={s.value} value={s.value}>
                      {sourceLabel(s.value)} ({s.count})
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Zone / location">
                <select className={inputClass} value={query.zone ?? ""} onChange={(e) => update({ zone: e.target.value || undefined })}>
                  <option value="">All zones</option>
                  {(summary?.zones ?? []).map((z) => (
                    <option key={z.value} value={z.value}>
                      {z.value} ({z.count})
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Device">
                <input
                  className={inputClass}
                  list="quality-devices"
                  placeholder="Any device"
                  defaultValue={query.device_id ?? ""}
                  key={query.device_id ?? ""}
                  onBlur={(e) => update({ device_id: e.target.value.trim() || undefined })}
                  onKeyDown={(e) => e.key === "Enter" && update({ device_id: e.currentTarget.value.trim() || undefined })}
                />
                <datalist id="quality-devices">
                  {(summary?.devices ?? []).map((d) => (
                    <option key={d.value} value={d.value} />
                  ))}
                </datalist>
              </Field>
              <Field label="From">
                <input type="datetime-local" className={inputClass} value={isoToLocalInput(query.occurred_from)}
                  onChange={(e) => update({ occurred_from: localInputToIso(e.target.value) })} />
              </Field>
              <Field label="Before">
                <input type="datetime-local" className={inputClass} value={isoToLocalInput(query.occurred_to)}
                  onChange={(e) => update({ occurred_to: localInputToIso(e.target.value) })} />
              </Field>
              <Field label="Sort">
                <select className={inputClass} value={query.sort} onChange={(e) => update({ sort: e.target.value })}>
                  {SORTS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="flex items-end">
                <Button variant="outline" className="w-full" onClick={resetFilters} disabled={!hasFilters}>
                  <RotateCcw /> Clear filters
                </Button>
              </div>
            </div>

            <p className="text-sm text-muted-foreground">
              {data && (
                <>
                  <span className="font-medium text-foreground">{data.total.toLocaleString()}</span> {hasFilters ? "matching issues" : "issues"}
                </>
              )}
            </p>

            {error ? (
              <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-6 text-center text-sm">
                <p className="font-medium text-destructive">Could not load quality issues</p>
                <p className="mt-1 text-muted-foreground">{error}</p>
              </div>
            ) : !data ? (
              <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Loading issues…
              </p>
            ) : data.items.length === 0 ? (
              <div className="rounded-md border border-dashed py-14 text-center">
                <p className="font-medium">{hasFilters ? "No issues match these filters" : "No quality issues found"}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {hasFilters
                    ? "Try clearing a filter."
                    : "Either the data is clean or nothing has been analysed yet. Import events on the Events page; analysis runs automatically."}
                </p>
                {hasFilters && (
                  <Button variant="outline" className="mt-4" onClick={resetFilters}>
                    Clear filters
                  </Button>
                )}
              </div>
            ) : (
              <div className={cn("overflow-x-auto rounded-md border transition-opacity", loading && "opacity-60")}>
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted/60 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">Severity</th>
                      <th className="px-3 py-2 font-medium">Issue</th>
                      <th className="px-3 py-2 font-medium">Device / zone</th>
                      <th className="px-3 py-2 font-medium">When</th>
                      <th className="px-3 py-2 text-right font-medium">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((issue) => (
                      <tr
                        key={issue.id}
                        tabIndex={0}
                        onClick={() => update({ issue: issue.id }, false)}
                        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && update({ issue: issue.id }, false)}
                        className={cn("cursor-pointer border-t align-top outline-none hover:bg-muted/50 focus-visible:bg-muted", selected === issue.id && "bg-muted")}
                      >
                        <td className="px-3 py-2">
                          <SeverityBadge severity={issue.severity} />
                        </td>
                        <td className="max-w-md px-3 py-2">
                          <div className="text-xs text-muted-foreground">{issueTypeLabel(issue.issue_type)}</div>
                          <div className="font-medium">{issue.title}</div>
                        </td>
                        <td className="px-3 py-2 text-xs">
                          <div className="font-mono">{issue.devices.slice(0, 2).join(", ") || "—"}{issue.devices.length > 2 && ` +${issue.devices.length - 2}`}</div>
                          <div className="text-muted-foreground">{issue.zones.slice(0, 2).join(", ")}</div>
                        </td>
                        <td className="px-3 py-2 text-xs whitespace-nowrap tabular-nums">{formatDateTime(issue.occurred_from)}</td>
                        <td className="px-3 py-2 text-right text-xs tabular-nums whitespace-nowrap">
                          {issue.event_count > 0 && `${issue.event_count} event${issue.event_count > 1 ? "s" : ""}`}
                          {issue.event_count === 0 && `${issue.submission_count} record${issue.submission_count > 1 ? "s" : ""}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
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

      {selected !== null && <QualityIssueDetailPanel issueId={selected} onClose={closeDetail} />}
    </div>
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
