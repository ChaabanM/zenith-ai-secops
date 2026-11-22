import { FileUp, Loader2, X } from "lucide-react";
import { useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api, ApiError, type IngestFormat } from "@/services/api";
import type { IngestSummary, RecordReport } from "@/types/events";

import { inputClass } from "./controls";

interface IngestPanelProps {
  onClose: () => void;
  onIngested: (summary: IngestSummary) => void;
}

function detectFormat(name: string): IngestFormat {
  return /\.(jsonl|ndjson)$/i.test(name) ? "jsonl" : "json";
}

export function IngestPanel({ onClose, onIngested }: IngestPanelProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [format, setFormat] = useState<IngestFormat>("json");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<IngestSummary | null>(null);

  const canSubmit = !busy && (file !== null || text.trim().length > 0);

  async function submit() {
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      const result = file
        ? await api.ingest(file, format, file.name)
        : await api.ingest(text, format, undefined);
      setSummary(result);
      onIngested(result);
    } catch (err) {
      if (err instanceof ApiError) {
        const ref = (err.details as { submission_id?: number } | undefined)?.submission_id;
        setError(`${err.message}${ref ? ` (raw request kept as submission #${ref})` : ""}`);
      } else {
        setError(err instanceof Error ? `Could not reach the backend: ${err.message}` : "Upload failed");
      }
    } finally {
      setBusy(false);
    }
  }

  function chooseFile(selected: File | null) {
    setFile(selected);
    setSummary(null);
    setError(null);
    if (selected) setFormat(detectFormat(selected.name));
  }

  return (
    <Card className="gap-4">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1.5">
            <CardTitle>Import events</CardTitle>
            <CardDescription>
              Upload a <code>.json</code> file (one event, an array, or <code>{"{\"events\": [...]}"}</code>) or a{" "}
              <code>.jsonl</code> file (one event per line, streamed). You can also paste JSON below. Every
              record is kept as evidence, including invalid ones and duplicates.
            </CardDescription>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close import panel">
            <X />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInput}
            type="file"
            accept=".json,.jsonl,.ndjson,application/json"
            className="hidden"
            onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
          />
          <Button variant="outline" onClick={() => fileInput.current?.click()}>
            <FileUp /> Choose file
          </Button>
          {file ? (
            <span className="flex items-center gap-2 text-sm">
              <span className="font-medium">{file.name}</span>
              <span className="text-muted-foreground">({(file.size / 1024).toFixed(1)} KB)</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  chooseFile(null);
                  if (fileInput.current) fileInput.current.value = "";
                }}
              >
                Remove
              </Button>
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">No file selected</span>
          )}
          <label className="ml-auto flex items-center gap-2 text-sm">
            Format
            <select
              className={cn(inputClass, "w-auto")}
              value={format}
              onChange={(e) => setFormat(e.target.value as IngestFormat)}
            >
              <option value="json">JSON</option>
              <option value="jsonl">JSONL (one per line)</option>
            </select>
          </label>
        </div>

        {!file && (
          <textarea
            className={cn(inputClass, "h-32 font-mono text-xs")}
            placeholder='{"event_id": "evt-1", "source_type": "radar", "device_id": "radar-07", "event_type": "speed_violation", "occurred_at": "2026-01-15T10:12:04Z"}'
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setSummary(null);
            }}
            aria-label="Paste events"
          />
        )}

        <div className="flex items-center gap-3">
          <Button onClick={submit} disabled={!canSubmit}>
            {busy && <Loader2 className="animate-spin" />}
            {busy ? "Ingesting…" : "Ingest"}
          </Button>
          <span className="text-xs text-muted-foreground">
            Sample data: <code>data/samples/demo_events.json</code>
          </span>
        </div>

        {error && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </p>
        )}
        {summary && <IngestResult summary={summary} />}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <div className={cn("text-lg font-semibold tabular-nums", tone)}>{value.toLocaleString()}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

function IngestResult({ summary }: { summary: IngestSummary }) {
  const problems = summary.records.filter((r) => r.status !== "accepted" || r.warnings.length > 0);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Batch {summary.batch_id}</span>
        <Badge variant="secondary">{summary.channel.toUpperCase()}</Badge>
        <span className="text-muted-foreground">processed in {(summary.duration_ms / 1000).toFixed(2)} s</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Received" value={summary.total_received} />
        <Stat label="Accepted" value={summary.accepted} tone="text-emerald-600" />
        <Stat label="Invalid" value={summary.invalid} tone={summary.invalid ? "text-destructive" : undefined} />
        <Stat label="Duplicate IDs" value={summary.duplicate_event_ids} tone={summary.duplicate_event_ids ? "text-amber-600" : undefined} />
        <Stat label="…conflicting" value={summary.conflicting_duplicates} />
        <Stat label="With warnings" value={summary.with_warnings} />
      </div>
      {problems.length > 0 && (
        <div className="max-h-72 overflow-auto rounded-md border">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-muted text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">{summary.channel === "jsonl" ? "Line" : "#"}</th>
                <th className="px-3 py-2 font-medium">Event ID</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
                <th className="px-3 py-2 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {problems.map((r) => (
                <RecordRow key={r.submission_id} record={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {summary.records_truncated && (
        <p className="text-xs text-muted-foreground">
          Large batch: only records with problems are listed here (up to the server limit). Every record is
          stored and can be inspected through <code>GET /submissions?batch_id={summary.batch_id}</code>.
        </p>
      )}
    </div>
  );
}

function RecordRow({ record }: { record: RecordReport }) {
  const tone =
    record.status === "invalid"
      ? "text-destructive"
      : record.status === "duplicate"
        ? "text-amber-600"
        : "text-muted-foreground";
  const label =
    record.status === "duplicate" ? `duplicate (${record.duplicate_kind})` : record.status === "accepted" ? "accepted with warnings" : "invalid";
  const issues = record.errors.length > 0 ? record.errors : record.warnings;
  return (
    <tr className="border-t align-top">
      <td className="px-3 py-2 tabular-nums">{record.position}</td>
      <td className="px-3 py-2 font-mono">{record.event_id ?? <span className="text-muted-foreground">(none)</span>}</td>
      <td className={cn("px-3 py-2 font-medium whitespace-nowrap", tone)}>{label}</td>
      <td className="px-3 py-2">
        <ul className="space-y-0.5">
          {issues.map((issue, i) => (
            <li key={i}>
              <span className="font-mono text-muted-foreground">{issue.field}</span>: {issue.message}
            </li>
          ))}
        </ul>
      </td>
    </tr>
  );
}
