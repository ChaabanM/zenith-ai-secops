import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Expandable detail. Collapsed content stays in the DOM and is always printed
 * (`print:block`), so a printed page keeps every detail even if it was collapsed on screen.
 */
export function Collapsible({
  label,
  children,
  defaultOpen = false,
  className,
  testId,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={className} data-testid={testId}>
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground print:hidden"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
        {label}
      </button>
      <div id={id} className={cn("mt-1.5", !open && "hidden print:block")} data-collapsed={!open}>
        {children}
      </div>
    </div>
  );
}

/**
 * Compact display of a long identifier: keeps the prefix and the distinguishing tail
 * ("evt-test-…-0001"), with the full value in the tooltip and accessible name.
 * Short identifiers (up to `max` characters) are shown unchanged.
 */
export function shortId(value: string, max = 18): string {
  if (value.length <= max) return value;
  return `${value.slice(0, 9)}…${value.slice(-7)}`;
}

export function ShortId({ value, className }: { value: string; className?: string }) {
  const short = shortId(value);
  if (short === value) return <span className={cn("font-mono", className)}>{value}</span>;
  return (
    <span className={cn("font-mono", className)} title={value} data-full-id={value}>
      <span aria-hidden>{short}</span>
      <span className="sr-only">{value}</span>
    </span>
  );
}

export const NOT_REPORTED_HINT = "The sensor did not report a confidence value. This is not the same as low confidence.";

/** A sensor's own confidence value, or an explicit "not reported" (never treated as zero). */
export function SensorConfidence({ value, lowBelow = 0.5 }: { value: number | null; lowBelow?: number }) {
  if (value === null || value === undefined) {
    return (
      <span className="text-muted-foreground italic" title={NOT_REPORTED_HINT} data-confidence="not-reported">
        not reported
      </span>
    );
  }
  return (
    <span className={cn("tabular-nums", value < lowBelow && "font-medium text-amber-700 dark:text-amber-300")} data-confidence={value}>
      {value.toFixed(2)}
      {value < lowBelow && <span className="sr-only"> (low)</span>}
    </span>
  );
}
