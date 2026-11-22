import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/services/api";
import type { IncidentDetail, IncidentStatus } from "@/types/incidents";

import { formatDateTime } from "../events/format";
import { StatusBadge } from "./badges";
import { STATUS_ACTIONS, STATUS_LABELS } from "./labels";

interface Props {
  incident: Pick<IncidentDetail, "id" | "status" | "status_note" | "status_updated_at">;
  /** Called with the backend's response after a successful update. */
  onUpdated: (incident: IncidentDetail) => void;
}

/**
 * Operator status controls. A change is only sent after explicit confirmation and is
 * only reported as successful after the backend confirms it. Nothing else is triggered.
 */
export function StatusControls({ incident, onUpdated }: Props) {
  const [proposed, setProposed] = useState<IncidentStatus | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Current status</span>
        <StatusBadge status={incident.status} />
        {incident.status_updated_at && (
          <span className="text-xs text-muted-foreground">set {formatDateTime(incident.status_updated_at)}</span>
        )}
      </div>
      {incident.status_note && (
        <p className="rounded-md border bg-muted/40 px-3 py-2 text-sm">
          <span className="text-xs font-medium text-muted-foreground">Operator note: </span>
          {incident.status_note}
        </p>
      )}
      <div className="flex flex-wrap gap-2" role="group" aria-label="Change incident status">
        {STATUS_ACTIONS.filter((a) => a.status !== incident.status).map((a) => (
          <Button
            key={a.status}
            size="sm"
            variant={a.status === "false_positive" || a.status === "open" ? "outline" : "secondary"}
            onClick={() => {
              setSuccess(null);
              setProposed(a.status);
            }}
          >
            {a.label}
          </Button>
        ))}
      </div>
      {success && (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">
          {success}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Status is recorded for the investigation only. It does not trigger enforcement, dispatch or any other action, and the
        correlation engine never changes it.
      </p>
      {proposed && (
        <ConfirmStatusDialog
          incidentId={incident.id}
          current={incident.status}
          proposed={proposed}
          onCancel={() => setProposed(null)}
          onConfirmed={(updated) => {
            setProposed(null);
            setSuccess(`Status saved: ${STATUS_LABELS[updated.status]}.`);
            onUpdated(updated);
          }}
        />
      )}
    </div>
  );
}

interface DialogProps {
  incidentId: number;
  current: IncidentStatus;
  proposed: IncidentStatus;
  onCancel: () => void;
  onConfirmed: (incident: IncidentDetail) => void;
}

export function ConfirmStatusDialog({ incidentId, current, proposed, onCancel, onConfirmed }: DialogProps) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, saving]);

  async function confirm() {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateIncidentStatus(incidentId, proposed, note);
      onConfirmed(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The status could not be saved.");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button className="absolute inset-0 bg-black/40" aria-label="Cancel status change" onClick={() => !saving && onCancel()} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="status-dialog-title"
        className="relative w-full max-w-md space-y-4 rounded-xl border bg-background p-5 shadow-xl"
      >
        <h2 id="status-dialog-title" className="text-base font-semibold">
          Change status of incident #{incidentId}?
        </h2>
        <dl className="grid grid-cols-[7rem_1fr] items-center gap-2 text-sm">
          <dt className="text-muted-foreground">Incident</dt>
          <dd className="font-mono">#{incidentId}</dd>
          <dt className="text-muted-foreground">Current</dt>
          <dd>
            <StatusBadge status={current} />
          </dd>
          <dt className="text-muted-foreground">New</dt>
          <dd>
            <StatusBadge status={proposed} />
          </dd>
        </dl>
        <label className="block space-y-1 text-sm">
          <span className="text-xs font-medium text-muted-foreground">Note (optional, replaces the previous note)</span>
          <textarea
            className="min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={saving}
            placeholder="Reason or context for this change"
          />
        </label>
        {error && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            Not saved: {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button ref={cancelRef} variant="outline" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={confirm} disabled={saving}>
            {saving && <Loader2 className="animate-spin" />}
            {saving ? "Saving…" : `Confirm: ${STATUS_LABELS[proposed]}`}
          </Button>
        </div>
      </div>
    </div>
  );
}
