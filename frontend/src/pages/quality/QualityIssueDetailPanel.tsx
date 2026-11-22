import { ExternalLink, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/services/api";
import type { Submission } from "@/types/events";
import type { EvidenceItem, QualityIssueDetail } from "@/types/quality";

import { SeverityBadge } from "../events/EventsTable";
import { formatConfidence, formatDateTime, humanize, prettyJson, sourceLabel } from "../events/format";
import { issueTypeLabel, roleLabel } from "./labels";

interface Props {
  issueId: number;
  onClose: () => void;
}

export function QualityIssueDetailPanel({ issueId, onClose }: Props) {
  const [issue, setIssue] = useState<QualityIssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setIssue(null);
    setError(null);
    api
      .getQualityIssue(issueId, controller.signal)
      .then(setIssue)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load issue");
      });
    return () => controller.abort();
  }, [issueId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="Quality issue detail">
      <button className="absolute inset-0 bg-black/30" aria-label="Close issue detail" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-2xl flex-col border-l bg-background shadow-xl">
        <header className="flex items-start justify-between gap-4 border-b px-6 py-4">
          <div className="min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              {issue && <SeverityBadge severity={issue.severity} />}
              {issue && <Badge variant="secondary">{issueTypeLabel(issue.issue_type)}</Badge>}
              {issue?.subtype && <span className="text-xs text-muted-foreground">{humanize(issue.subtype)}</span>}
            </div>
            <h2 className="text-base font-semibold">{issue?.title ?? "…"}</h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X />
          </Button>
        </header>
        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {error && <p className="text-sm text-destructive">{error}</p>}
          {!issue && !error && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading issue…
            </p>
          )}
          {issue && <Body issue={issue} />}
        </div>
      </aside>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function Body({ issue }: { issue: QualityIssueDetail }) {
  const scalarDetails = Object.entries(issue.details).filter(
    ([, v]) => v !== null && (typeof v === "string" || typeof v === "number" || typeof v === "boolean"),
  );
  return (
    <>
      <Section title="What is wrong">
        <p className="text-sm leading-relaxed">{issue.description}</p>
      </Section>

      <Section title="Why it was flagged">
        <p className="rounded-md border bg-muted/40 p-3 text-sm leading-relaxed">{issue.detection_reason}</p>
      </Section>

      {(issue.uncertainty || issue.missing_evidence) && (
        <div className="space-y-2 rounded-md border border-amber-300/60 bg-amber-50 p-3 text-sm dark:bg-amber-500/10">
          {issue.uncertainty && (
            <p>
              <span className="font-medium">Uncertainty: </span>
              {issue.uncertainty}
            </p>
          )}
          {issue.missing_evidence && (
            <p>
              <span className="font-medium">Missing evidence: </span>
              {issue.missing_evidence}
            </p>
          )}
        </div>
      )}

      <Section title="Summary">
        <dl className="divide-y">
          <Fact label={issue.time_basis === "ingested_at" ? "Received" : "When"}>
            {formatDateTime(issue.occurred_from)}
            {issue.occurred_to !== issue.occurred_from && ` → ${formatDateTime(issue.occurred_to)}`}
            {issue.time_basis === "ingested_at" && (
              <span className="text-muted-foreground"> (no valid event time; ingestion time shown)</span>
            )}
          </Fact>
          <Fact label="Devices">{issue.devices.join(", ") || "—"}</Fact>
          <Fact label="Zones">{issue.zones.join(", ") || "—"}</Fact>
          <Fact label="Sources">{issue.sources.map(sourceLabel).join(", ") || "—"}</Fact>
          <Fact label="Rule">
            <span className="font-mono text-xs">
              {issue.rule_id} v{issue.rule_version}
            </span>
            {issue.confidence !== null && (
              <span className="text-muted-foreground" data-testid="rule-score" title="A deterministic rule score, not a calibrated probability that the finding is correct.">
                {" "}· Rule confidence score: {issue.confidence.toFixed(2)} (heuristic)
                <span className="block text-xs">A deterministic rule score, not a calibrated probability that the finding is correct.</span>
              </span>
            )}
          </Fact>
          <Fact label="Detected">
            {formatDateTime(issue.first_detected_at)}
            <span className="text-muted-foreground"> (last re-detected by the rule {formatDateTime(issue.last_detected_at)})</span>
          </Fact>
          {scalarDetails.map(([key, value]) => (
            <Fact key={key} label={humanize(key)}>
              {String(value)}
            </Fact>
          ))}
        </dl>
      </Section>

      <Section title={`Evidence (${issue.evidence_total})`}>
        <p className="text-xs text-muted-foreground">
          The source records behind this finding. Open an event to see its full details, or view the submission exactly as it
          was received.
        </p>
        <div className="space-y-2">
          {issue.evidence.map((item, i) => (
            <EvidenceRow key={i} item={item} />
          ))}
        </div>
        {issue.evidence_truncated && (
          <p className="text-xs text-muted-foreground">Showing the first {issue.evidence.length} of {issue.evidence_total} records.</p>
        )}
      </Section>
    </>
  );
}

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const [raw, setRaw] = useState<Submission | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const { event, submission } = item;

  async function toggleRaw() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (!raw && submission) {
      setLoading(true);
      try {
        setRaw(await api.getSubmission(submission.id));
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant="outline">{roleLabel(item.role)}</Badge>
          <span className="font-mono text-xs">{item.external_event_id ?? "(no event id)"}</span>
        </div>
        <div className="flex gap-1">
          {event && (
            <Button variant="ghost" size="sm" asChild>
              <Link to={`/events?event=${event.id}`}>
                Open event <ExternalLink />
              </Link>
            </Button>
          )}
          {submission && (
            <Button variant="ghost" size="sm" onClick={toggleRaw}>
              {open ? "Hide raw" : "View raw"}
            </Button>
          )}
        </div>
      </div>
      {event && (
        <p className="mt-1.5 text-muted-foreground">
          {sourceLabel(event.source_type)} · {humanize(event.event_type)} · <span className="font-mono text-xs">{event.device_id}</span>
          {event.zone && ` · ${event.zone}`} · {formatDateTime(event.occurred_at)}
          {` · sensor confidence ${formatConfidence(event.confidence)}`}
        </p>
      )}
      {submission && (
        <p className="mt-1 text-xs text-muted-foreground">
          Submission #{submission.id} · batch {submission.batch_id} · record {submission.position} · {submission.status}
          {submission.duplicate_kind && ` (${submission.duplicate_kind})`}
        </p>
      )}
      {submission && submission.errors.length > 0 && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-xs text-destructive">
          {submission.errors.map((e, i) => (
            <li key={i}>
              <span className="font-mono">{e.field}</span>: {e.message}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">
          {loading ? "Loading…" : raw ? prettyJson(raw.raw_content) : "Unavailable"}
        </pre>
      )}
    </div>
  );
}
