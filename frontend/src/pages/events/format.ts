import type { Severity } from "@/types/events";

export const SEVERITY_STYLES: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200",
  low: "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200",
  informational: "bg-muted text-muted-foreground",
};

const SOURCE_LABELS: Record<string, string> = {
  cctv: "CCTV",
  anpr: "ANPR",
  face_recognition: "Face recognition",
  mobile_phone_detection: "Mobile-phone detection",
  seatbelt_detection: "Seatbelt detection",
  traffic_violation: "Traffic violation",
  radar: "Radar",
  access_control: "Access control",
  perimeter_intrusion: "Perimeter intrusion",
  device_health: "Device health",
  operator_report: "Operator report",
  emergency_alarm: "Emergency alarm",
  gps_fleet: "GPS / fleet",
  network_security: "Network security",
  weather_environmental: "Weather / environmental",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

export function humanize(value: string): string {
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(key: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(undefined, options);
    formatters.set(key, f);
  }
  return f;
}

/**
 * Date and time with a time-zone label (e.g. "GMT+4", "UTC"). Without `timeZone` the browser's zone is used, and the
 * label says which, so times on different pages are never ambiguous.
 */
export function formatDateTime(value: string | null | undefined, timeZone?: string): string {
  if (!value) return "—";
  return formatter(`dt:${timeZone ?? ""}`, {
    year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    timeZoneName: "short", ...(timeZone ? { timeZone } : {}),
  }).format(new Date(value));
}

/** Offset of `timeZone` from UTC at `instant`, in minutes. */
function offsetMinutes(instant: number, timeZone: string): number {
  const parts = formatter(`off:${timeZone}`, {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(instant));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return Math.round((asUtc - Math.floor(instant / 1000) * 1000) / 60000);
}

/** `datetime-local` wall time interpreted in `timeZone` (IANA) -> ISO string in UTC, or undefined. */
export function zonedInputToIso(value: string, timeZone: string): string | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!m) return undefined;
  const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  let instant = wall - offsetMinutes(wall, timeZone) * 60000;
  instant = wall - offsetMinutes(instant, timeZone) * 60000; // second pass settles DST transitions
  return new Date(instant).toISOString();
}

/** ISO instant -> `datetime-local` wall time in `timeZone`. */
export function isoToZonedInput(value: string | undefined | null, timeZone: string): string {
  if (!value) return "";
  const instant = new Date(value).getTime();
  if (Number.isNaN(instant)) return "";
  const local = new Date(instant + offsetMinutes(instant, timeZone) * 60000);
  return local.toISOString().slice(0, 16);
}

/** Label for an hourly bucket ("YYYY-MM-DD HH" in UTC) shown in `timeZone`. */
export function hourLabel(hourUtc: string, timeZone: string): string {
  const d = new Date(`${hourUtc.replace(" ", "T")}:00:00Z`);
  return formatter(`h:${timeZone}`, { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
}

/** Shorter form for dense tables (no seconds), with the time-zone label. */
export function formatCompactDateTime(value: string | null, timeZone?: string): string {
  if (!value) return "—";
  return formatter(`cdt:${timeZone ?? ""}`, {
    year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    ...(timeZone ? { timeZone } : {}),
  }).format(new Date(value));
}

export function formatDelay(seconds: number | null): string {
  if (seconds === null) return "unknown";
  const sign = seconds < 0 ? "−" : "";
  const abs = Math.abs(seconds);
  if (abs < 60) return `${sign}${abs.toFixed(abs < 10 ? 1 : 0)} s`;
  if (abs < 3600) return `${sign}${(abs / 60).toFixed(1)} min`;
  if (abs < 86400) return `${sign}${(abs / 3600).toFixed(1)} h`;
  return `${sign}${(abs / 86400).toFixed(1)} d`;
}

/** Sensor-reported confidence as reported (0–1, two decimals); never a percentage, which reads like a probability. */
export function formatConfidence(value: number | null): string {
  return value === null ? "not reported" : value.toFixed(2);
}

/** `datetime-local` value (viewer's local time) -> ISO string with offset, or undefined. */
export function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/** ISO string -> `datetime-local` value in the viewer's local time. */
export function isoToLocalInput(value: string | undefined | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw; // malformed evidence is shown exactly as received
  }
}
