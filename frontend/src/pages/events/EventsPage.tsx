import { ChevronLeft, ChevronRight, Loader2, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import type { EventFacets, EventQuery, EventSort, EventSummary, Page } from "@/types/events";

import { inputClass } from "./controls";
import { EventDetailPanel } from "./EventDetailPanel";
import { EventFilters, type FilterValues } from "./EventFilters";
import { EventsTable } from "./EventsTable";
import { IngestPanel } from "./IngestPanel";

const FILTER_KEYS = ["severity", "source_type", "device_id", "zone", "occurred_from", "occurred_to"] as const;
const PAGE_SIZES = [25, 50, 100, 200];
const SORTS: { value: EventSort; label: string }[] = [
  { value: "-occurred_at", label: "Newest first" },
  { value: "occurred_at", label: "Oldest first" },
  { value: "-id", label: "Most recently ingested" },
];

function readQuery(params: URLSearchParams): EventQuery {
  const page = Math.max(1, Number(params.get("page")) || 1);
  const size = Number(params.get("page_size"));
  const sort = params.get("sort") as EventSort | null;
  const query: EventQuery = {
    page,
    page_size: PAGE_SIZES.includes(size) ? size : 50,
    sort: sort && SORTS.some((s) => s.value === sort) ? sort : "-occurred_at",
  };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value) query[key] = value;
  }
  return query;
}

export function EventsPage() {
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => readQuery(params), [params]);
  const [data, setData] = useState<Page<EventSummary> | null>(null);
  const [facets, setFacets] = useState<EventFacets | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [showImport, setShowImport] = useState(() => params.get("import") === "1"); // /events?import=1 opens the import panel
  const selected = Number(params.get("event")) || null;

  const update = useCallback(
    (patch: Record<string, string | number | undefined | null>, resetPage = true) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            if (value === undefined || value === null || value === "") next.delete(key);
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
      .listEvents(query, controller.signal)
      .then((page) => {
        setData(page);
        setError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Failed to load events");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, reloadKey]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .eventFacets(controller.signal)
      .then(setFacets)
      .catch(() => {
        /* filters still work without facet suggestions */
      });
    return () => controller.abort();
  }, [reloadKey]);

  const filters: FilterValues = Object.fromEntries(FILTER_KEYS.map((k) => [k, query[k]]));
  const hasFilters = FILTER_KEYS.some((k) => query[k]);
  const resetFilters = () => update(Object.fromEntries(FILTER_KEYS.map((k) => [k, undefined])));
  const closeDetail = useCallback(() => update({ event: undefined }, false), [update]);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Events</h2>
          <p className="text-sm text-muted-foreground">
            Normalized source events from all sensors. Select a row to see its original evidence.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setReloadKey((k) => k + 1)} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} /> Refresh
          </Button>
          <Button onClick={() => setShowImport((v) => !v)}>
            <Upload /> Import events
          </Button>
        </div>
      </div>

      {showImport && <IngestPanel onClose={() => setShowImport(false)} onIngested={() => setReloadKey((k) => k + 1)} />}

      <Card className="gap-4 py-4">
        <CardContent className="space-y-4 px-4">
          <EventFilters values={filters} facets={facets} onChange={(patch) => update({ ...patch })} onReset={resetFilters} />

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span className="text-muted-foreground" aria-live="polite">
              {data ? (
                <>
                  <span className="font-medium text-foreground">{data.total.toLocaleString()}</span>{" "}
                  {hasFilters ? "matching events" : "events"}
                  {facets && hasFilters && ` of ${facets.total_events.toLocaleString()}`}
                </>
              ) : (
                " "
              )}
            </span>
            <div className="flex items-center gap-2">
              <select
                className={cn(inputClass, "w-auto")}
                value={query.sort}
                onChange={(e) => update({ sort: e.target.value })}
                aria-label="Sort order"
              >
                {SORTS.map((s) => (
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
              <p className="font-medium text-destructive">Could not load events</p>
              <p className="mt-1 text-muted-foreground">{error}</p>
              <Button variant="outline" className="mt-3" onClick={() => setReloadKey((k) => k + 1)}>
                Try again
              </Button>
            </div>
          ) : !data ? (
            <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading events…
            </p>
          ) : data.items.length === 0 ? (
            <EmptyState filtered={hasFilters} onReset={resetFilters} onImport={() => setShowImport(true)} />
          ) : (
            <div className={cn("transition-opacity", loading && "opacity-60")}>
              <EventsTable events={data.items} selectedId={selected} onSelect={(id) => update({ event: id }, false)} />
            </div>
          )}

          {data && data.total > 0 && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                Page {data.page.toLocaleString()} of {Math.max(1, data.total_pages).toLocaleString()}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={query.page <= 1 || loading}
                  onClick={() => update({ page: query.page - 1 }, false)}
                >
                  <ChevronLeft /> Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!data.has_next || loading}
                  onClick={() => update({ page: query.page + 1 }, false)}
                >
                  Next <ChevronRight />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {selected !== null && <EventDetailPanel eventId={selected} onClose={closeDetail} />}
    </div>
  );
}

function EmptyState({ filtered, onReset, onImport }: { filtered: boolean; onReset: () => void; onImport: () => void }) {
  return (
    <div className="rounded-md border border-dashed py-14 text-center">
      {filtered ? (
        <>
          <p className="font-medium">No events match these filters</p>
          <p className="mt-1 text-sm text-muted-foreground">Try widening the time range or clearing a filter.</p>
          <Button variant="outline" className="mt-4" onClick={onReset}>
            Clear filters
          </Button>
        </>
      ) : (
        <>
          <p className="font-medium">No events ingested yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Import <code>data/samples/demo_events.json</code> or a generated JSONL file to get started.
          </p>
          <Button className="mt-4" onClick={onImport}>
            <Upload /> Import events
          </Button>
        </>
      )}
    </div>
  );
}
