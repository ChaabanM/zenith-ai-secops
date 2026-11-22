import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SEVERITIES, type EventFacets } from "@/types/events";

import { inputClass } from "./controls";
import { humanize, isoToLocalInput, localInputToIso, sourceLabel } from "./format";

export interface FilterValues {
  severity?: string;
  source_type?: string;
  device_id?: string;
  zone?: string;
  occurred_from?: string;
  occurred_to?: string;
}

interface EventFiltersProps {
  values: FilterValues;
  facets: EventFacets | null;
  onChange: (patch: FilterValues) => void;
  onReset: () => void;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-medium text-muted-foreground">
      {label}
      {children}
    </label>
  );
}

export function EventFilters({ values, facets, onChange, onReset }: EventFiltersProps) {
  const active = Object.values(values).some(Boolean);
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-7">
      <Field label="Severity">
        <select
          className={inputClass}
          value={values.severity ?? ""}
          onChange={(e) => onChange({ severity: e.target.value || undefined })}
        >
          <option value="">All severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Source">
        <select
          className={inputClass}
          value={values.source_type ?? ""}
          onChange={(e) => onChange({ source_type: e.target.value || undefined })}
        >
          <option value="">All sources</option>
          {(facets?.sources ?? []).map((f) => (
            <option key={f.value} value={f.value}>
              {sourceLabel(f.value)} ({f.count.toLocaleString()})
            </option>
          ))}
        </select>
      </Field>
      <Field label="Zone / location">
        <select
          className={inputClass}
          value={values.zone ?? ""}
          onChange={(e) => onChange({ zone: e.target.value || undefined })}
        >
          <option value="">All zones</option>
          {(facets?.zones ?? []).map((f) => (
            <option key={f.value} value={f.value}>
              {f.value} ({f.count.toLocaleString()})
            </option>
          ))}
        </select>
      </Field>
      <Field label="Device">
        <input
          className={inputClass}
          list="device-options"
          placeholder="Any device"
          defaultValue={values.device_id ?? ""}
          key={values.device_id ?? ""}
          onBlur={(e) => onChange({ device_id: e.target.value.trim() || undefined })}
          onKeyDown={(e) => {
            if (e.key === "Enter") onChange({ device_id: e.currentTarget.value.trim() || undefined });
          }}
        />
        <datalist id="device-options">
          {(facets?.devices ?? []).map((f) => (
            <option key={f.value} value={f.value} />
          ))}
        </datalist>
      </Field>
      <Field label="Occurred from">
        <input
          type="datetime-local"
          className={inputClass}
          value={isoToLocalInput(values.occurred_from)}
          onChange={(e) => onChange({ occurred_from: localInputToIso(e.target.value) })}
        />
      </Field>
      <Field label="Occurred before">
        <input
          type="datetime-local"
          className={inputClass}
          value={isoToLocalInput(values.occurred_to)}
          onChange={(e) => onChange({ occurred_to: localInputToIso(e.target.value) })}
        />
      </Field>
      <div className="flex items-end">
        <Button variant="outline" className="w-full" onClick={onReset} disabled={!active}>
          <RotateCcw /> Clear filters
        </Button>
      </div>
    </div>
  );
}
