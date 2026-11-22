import { NOT_REPORTED_HINT, SensorConfidence, ShortId } from "@/components/evidence";
import { cn } from "@/lib/utils";
import type { EventSummary, Severity } from "@/types/events";

import { formatDateTime, formatDelay, humanize, SEVERITY_STYLES, sourceLabel } from "./format";

export function SeverityBadge({ severity }: { severity: Severity | null }) {
  if (!severity) {
    return <span className="text-xs text-muted-foreground italic">unknown</span>;
  }
  return (
    <span className={cn("inline-flex rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", SEVERITY_STYLES[severity])}>
      {humanize(severity)}
    </span>
  );
}

interface EventsTableProps {
  events: EventSummary[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

const LATE_SECONDS = 300;

export function EventsTable({ events, selectedId, onSelect }: EventsTableProps) {
  return (
    <div className="space-y-1">
    <p className="text-xs text-muted-foreground md:hidden" data-testid="scroll-hint">Scroll the table sideways to see every column.</p>
    <div className="relative overflow-x-auto rounded-md border" data-testid="events-scroll">
      <table className="w-full min-w-[60rem] text-left text-sm">
        <thead className="bg-muted/60 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Occurred</th>
            <th className="px-3 py-2 font-medium">Event ID</th>
            <th className="px-3 py-2 font-medium">Severity</th>
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Event type</th>
            <th className="px-3 py-2 font-medium">Device</th>
            <th className="px-3 py-2 font-medium">Zone</th>
            <th className="px-3 py-2 text-right font-medium" title={`Confidence reported by the sensor (0–1). ${NOT_REPORTED_HINT}`}>
              Sensor confidence
            </th>
            <th className="px-3 py-2 text-right font-medium">Arrival delay</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => {
            const late = event.delay_seconds !== null && (event.delay_seconds > LATE_SECONDS || event.delay_seconds < 0);
            return (
              <tr
                key={event.id}
                tabIndex={0}
                onClick={() => onSelect(event.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(event.id);
                  }
                }}
                aria-selected={selectedId === event.id}
                className={cn(
                  "cursor-pointer border-t outline-none hover:bg-muted/50 focus-visible:bg-muted",
                  selectedId === event.id && "bg-muted",
                )}
              >
                <td className="px-3 py-2 whitespace-nowrap tabular-nums">{formatDateTime(event.occurred_at)}</td>
                <td className="px-3 py-2 text-xs whitespace-nowrap">
                  <ShortId value={event.event_id} />
                </td>
                <td className="px-3 py-2">
                  <SeverityBadge severity={event.severity} />
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{sourceLabel(event.source_type)}</td>
                <td className="px-3 py-2 whitespace-nowrap">{humanize(event.event_type)}</td>
                <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">{event.device_id}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {event.location.zone ?? <span className="text-muted-foreground italic">none</span>}
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <SensorConfidence value={event.confidence} />
                </td>
                <td className={cn("px-3 py-2 text-right whitespace-nowrap tabular-nums", late && "text-amber-600")}>
                  {formatDelay(event.delay_seconds)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </div>
  );
}
