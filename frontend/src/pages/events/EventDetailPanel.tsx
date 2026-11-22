import { Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import type { EventDetail, Submission, ValidationIssue } from "@/types/events";
import type { IncidentSummary } from "@/types/incidents";

import { SeverityBadge } from "./EventsTable";
import { formatConfidence, formatDateTime, formatDelay, humanize, prettyJson, sourceLabel } from "./format";

interface EventDetailPanelProps {
  eventId: number;
  onClose: () => void;
  /** When opened from an incident, that incident is marked instead of linked. */
  currentIncidentId?: number;
}

export function EventDetailPanel({ eventId, onClose, currentIncidentId }: EventDetailPanelProps) {
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setError(null);
    api
      .getEvent(eventId, controller.signal)
      .then(setDetail)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load event");
      });
    return () => controller.abort();
  }, [eventId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="Event detail">
      <button className="absolute inset-0 bg-black/30" aria-label="Close event detail" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-2xl flex-col border-l bg-background shadow-xl">
        <header className="flex items-start justify-between gap-4 border-b px-6 py-4">
          <div className="min-w-0 space-y-1">
            <p className="text-xs text-muted-foreground">Source event ID</p>
            <h2 className="truncate font-mono text-base font-semibold">{detail?.event_id ?? "…"}</h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X />
          </Button>
        </header>
        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {error && <p className="text-sm text-destructive">{error}</p>}
          {!detail && !error && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading event…
            </p>
          )}
          {detail && <DetailBody detail={detail} currentIncidentId={currentIncidentId} />}
        </div>
      </aside>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function Section({ title, children, description }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

const unknown = <span className="text-muted-foreground italic">not provided</span>;

function DetailBody({ detail, currentIncidentId }: { detail: EventDetail; currentIncidentId?: number }) {
  const { location, submission } = detail;
  return (
    <>
      <Section title="Event">
        <dl className="divide-y">
          <Row label="Type">{humanize(detail.event_type)}</Row>
          <Row label="Severity">
            <SeverityBadge severity={detail.severity} />
          </Row>
          <Row label="Source">{sourceLabel(detail.source_type)}</Row>
          <Row label="Device">
            <span className="font-mono">{detail.device_id}</span>
          </Row>
          <Row label="Occurred">{formatDateTime(detail.occurred_at)}</Row>
          <Row label="Received">{detail.received_at ? formatDateTime(detail.received_at) : unknown}</Row>
          <Row label="Arrival delay">
            <span className={cn(detail.delay_seconds !== null && detail.delay_seconds < 0 && "text-amber-600")}>
              {formatDelay(detail.delay_seconds)}
              {detail.delay_seconds !== null && detail.delay_seconds < 0 && " (received before it occurred — possible clock skew)"}
            </span>
          </Row>
          <Row label="Ingested">{formatDateTime(detail.ingested_at)}</Row>
          <Row label="Zone">{location.zone ?? unknown}</Row>
          <Row label="Coordinates">
            {location.latitude !== null ? `${location.latitude}, ${location.longitude}` : unknown}
          </Row>
          <Row label="Sensor confidence">
            {detail.confidence !== null ? (
              formatConfidence(detail.confidence)
            ) : (
              <span className="text-muted-foreground italic">not reported by the sensor (not the same as low confidence)</span>
            )}
          </Row>
        </dl>
      </Section>

      <RelatedIncidents eventPk={detail.id} currentIncidentId={currentIncidentId} />

      {detail.quality_issues.length > 0 && (
        <Section title={`Data-quality issues (${detail.quality_issues.length})`} description="Findings that involve this event.">
          <ul className="space-y-1.5">
            {detail.quality_issues.map((issue) => (
              <li key={issue.id} className="flex items-start gap-2 text-sm">
                <SeverityBadge severity={issue.severity} />
                <Link className="underline-offset-4 hover:underline" to={`/data-quality?issue=${issue.id}`}>
                  {issue.title}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Sensor details" description="Source-specific payload as reported by the device.">
        <PayloadView payload={detail.payload} />
      </Section>

      <Section
        title="Provenance"
        description="The submission that created this event. Later submissions with the same event ID never change it."
      >
        <dl className="divide-y">
          <Row label="Submission">#{submission.id}</Row>
          <Row label="Batch">
            <span className="font-mono text-xs">{submission.batch_id}</span> · record {submission.position}
          </Row>
          <Row label="Raw SHA-256">
            <span className="font-mono text-xs">{submission.content_sha256}</span>
          </Row>
        </dl>
        <IssueList title="Validation notes" issues={submission.warnings} />
      </Section>

      <Section title="Original evidence" description="The record exactly as submitted (formatted for reading).">
        <Evidence submission={submission} />
      </Section>

      {detail.duplicate_submissions.length > 0 && (
        <Section
          title={`Repeated submissions (${detail.duplicate_submissions.length})`}
          description="Other submissions that used this event ID. They are preserved as evidence but did not overwrite the event."
        >
          <div className="space-y-3">
            {detail.duplicate_submissions.map((dup) => (
              <div key={dup.id} className="space-y-2 rounded-md border p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant={dup.duplicate_kind === "conflicting" ? "destructive" : "secondary"}>
                    {dup.duplicate_kind === "conflicting" ? "Conflicting duplicate" : "Exact duplicate"}
                  </Badge>
                  <span className="text-muted-foreground">
                    submission #{dup.id} · {formatDateTime(dup.ingested_at)}
                  </span>
                </div>
                {dup.conflicting_fields && dup.conflicting_fields.length > 0 && (
                  <p className="text-sm">
                    Differs in: <span className="font-medium">{dup.conflicting_fields.join(", ")}</span>
                  </p>
                )}
                <Evidence submission={dup} />
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}

function PayloadView({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload);
  if (entries.length === 0) return <p className="text-sm text-muted-foreground italic">No payload provided.</p>;
  return (
    <dl className="divide-y rounded-md border px-3">
      {entries.map(([key, value]) => (
        <Row key={key} label={humanize(key)}>
          {value === null ? (
            <span className="text-muted-foreground italic">null (unknown)</span>
          ) : typeof value === "object" ? (
            <code className="text-xs">{JSON.stringify(value)}</code>
          ) : (
            String(value)
          )}
        </Row>
      ))}
    </dl>
  );
}

function IssueList({ title, issues }: { title: string; issues: ValidationIssue[] }) {
  if (issues.length === 0) return null;
  return (
    <div className="rounded-md border border-amber-300/60 bg-amber-50 p-3 text-sm dark:bg-amber-500/10">
      <p className="mb-1 font-medium">{title}</p>
      <ul className="list-disc space-y-0.5 pl-5">
        {issues.map((issue, i) => (
          <li key={i}>
            <span className="font-mono text-xs">{issue.field}</span> — {issue.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Evidence({ submission }: { submission: Submission }) {
  return (
    <div className="space-y-1">
      <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 font-mono text-xs leading-relaxed">
        {prettyJson(submission.raw_content)}
      </pre>
      <p className="text-xs text-muted-foreground">
        {submission.raw_size_bytes.toLocaleString()} bytes
        {submission.raw_truncated && " · truncated: the stored evidence exceeds the size limit"}
      </p>
    </div>
  );
}

/** Incidents whose evidence includes this event (GET /incidents?event=<id>). */
function RelatedIncidents({ eventPk, currentIncidentId }: { eventPk: number; currentIncidentId?: number }) {
  const [items, setItems] = useState<IncidentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setItems(null);
    setError(null);
    api
      .listIncidents({ event: eventPk, page: 1, page_size: 10, sort: "priority" }, controller.signal)
      .then((page) => setItems(page.items))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load incidents");
      });
    return () => controller.abort();
  }, [eventPk]);

  return (
    <Section title="Related incidents" description="Correlated incidents that use this event as evidence.">
      {error ? (
        <p className="text-sm text-destructive">Could not load related incidents: {error}</p>
      ) : !items ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground italic" data-testid="no-related-incidents">
          This event is not part of any active incident.
        </p>
      ) : (
        <ul className="space-y-1.5" data-testid="related-incidents">
          {items.map((i) => (
            <li key={i.id} className="flex items-start gap-2 text-sm">
              <SeverityBadge severity={i.severity} />
              {i.id === currentIncidentId ? (
                <span>
                  #{i.id} {i.title} <span className="text-muted-foreground">(this incident)</span>
                </span>
              ) : (
                <Link className="underline-offset-4 hover:underline" to={`/incidents/${i.id}`}>
                  #{i.id} {i.title}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
