import type {
  EventDetail,
  EventFacets,
  EventQuery,
  EventSummary,
  IngestSummary,
  Page,
  Submission,
} from "@/types/events";
import type { ApiErrorBody, HealthResponse } from "@/types/health";
import type { Briefing, BriefingPeriods, BriefingQuery } from "@/types/briefing";
import type { DashboardQuery, DashboardSummary } from "@/types/dashboard";
import type {
  CorrelationRun,
  IncidentDetail,
  IncidentQuery,
  IncidentStatus,
  IncidentsSummary,
  IncidentSummary,
} from "@/types/incidents";
import type { IssueQuery, QualityIssueDetail, QualityIssueSummary, QualityRun, QualitySummary } from "@/types/quality";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(
      response.status,
      err?.code ?? "http_error",
      err?.message ?? response.statusText,
      err?.details,
    );
  }
  return body as T;
}

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export type IngestFormat = "json" | "jsonl";

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>("/health", { signal }),

  listEvents: (query: EventQuery, signal?: AbortSignal) =>
    request<Page<EventSummary>>(`/events${queryString({ ...query })}`, { signal }),

  getEvent: (id: number, signal?: AbortSignal) => request<EventDetail>(`/events/${id}`, { signal }),

  eventFacets: (signal?: AbortSignal) => request<EventFacets>("/events/facets", { signal }),

  getSubmission: (id: number, signal?: AbortSignal) => request<Submission>(`/submissions/${id}`, { signal }),

  listQualityIssues: (query: IssueQuery, signal?: AbortSignal) =>
    request<Page<QualityIssueSummary>>(`/quality-issues${queryString({ ...query })}`, { signal }),

  getQualityIssue: (id: number, signal?: AbortSignal) => request<QualityIssueDetail>(`/quality-issues/${id}`, { signal }),

  qualitySummary: (signal?: AbortSignal) => request<QualitySummary>("/quality-issues/summary", { signal }),

  listIncidents: (query: IncidentQuery, signal?: AbortSignal) =>
    request<Page<IncidentSummary>>(`/incidents${queryString({ ...query })}`, { signal }),

  incidentsSummary: (signal?: AbortSignal) => request<IncidentsSummary>("/incidents/summary", { signal }),

  getIncident: (id: number, signal?: AbortSignal) => request<IncidentDetail>(`/incidents/${id}`, { signal }),

  /** Operator status change. Only called after explicit confirmation in the UI. */
  updateIncidentStatus: (id: number, status: IncidentStatus, note?: string) =>
    request<IncidentDetail>(`/incidents/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note: note?.trim() ? note.trim() : null }),
    }),

  briefing: (query: BriefingQuery, signal?: AbortSignal) =>
    request<Briefing>(`/briefing${queryString({ ...query })}`, { signal }),

  dashboard: (query: DashboardQuery, signal?: AbortSignal) =>
    request<DashboardSummary>(`/dashboard/summary${queryString({ ...query })}`, { signal }),

  briefingPeriods: (signal?: AbortSignal) => request<BriefingPeriods>("/briefing/periods", { signal }),

  correlate: () => request<CorrelationRun>("/incidents/correlate", { method: "POST" }),

  analyzeQuality: () => request<QualityRun>("/quality-issues/analyze", { method: "POST" }),

  /**
   * Submit a JSON document (single event, array or {"events": [...]}) or JSONL text.
   * A File/Blob body is streamed by the browser, so large JSONL files are not parsed client-side.
   * A request where every record is invalid returns HTTP 422 whose details carry the summary.
   */
  ingest: async (body: Blob | string, format: IngestFormat, filename?: string): Promise<IngestSummary> => {
    const contentType = format === "jsonl" ? "application/x-ndjson" : "application/json";
    try {
      return await request<IngestSummary>(`/ingest${queryString({ filename })}`, {
        method: "POST",
        headers: { "Content-Type": contentType },
        body,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && isIngestSummary(err.details)) {
        return err.details;
      }
      throw err;
    }
  },
};

function isIngestSummary(value: unknown): value is IngestSummary {
  return typeof value === "object" && value !== null && "batch_id" in value && "records" in value;
}
