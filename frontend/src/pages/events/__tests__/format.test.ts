import { describe, expect, it } from "vitest";

import { formatDateTime, hourLabel, isoToZonedInput, zonedInputToIso } from "../format";

describe("time-zone helpers", () => {
  it("interprets datetime-local values in the chosen zone, not the browser's", () => {
    expect(zonedInputToIso("2026-01-15T06:00", "Asia/Dubai")).toBe("2026-01-15T02:00:00.000Z");
    expect(zonedInputToIso("2026-01-15T06:00", "UTC")).toBe("2026-01-15T06:00:00.000Z");
    expect(zonedInputToIso("2026-07-01T12:00", "Europe/London")).toBe("2026-07-01T11:00:00.000Z"); // BST
    expect(zonedInputToIso("2026-03-29T03:30", "Europe/London")).toBe("2026-03-29T02:30:00.000Z"); // just after the DST change
    expect(zonedInputToIso("not a date", "UTC")).toBeUndefined();
  });

  it("round-trips ISO instants through the zoned input", () => {
    expect(isoToZonedInput("2026-01-15T02:00:00Z", "Asia/Dubai")).toBe("2026-01-15T06:00");
    expect(isoToZonedInput("2026-01-15T02:00:00Z", "UTC")).toBe("2026-01-15T02:00");
    expect(zonedInputToIso(isoToZonedInput("2026-01-14T20:00:00Z", "Asia/Dubai"), "Asia/Dubai")).toBe("2026-01-14T20:00:00.000Z");
    expect(isoToZonedInput(undefined, "UTC")).toBe("");
  });

  it("labels every formatted time with its zone", () => {
    expect(formatDateTime("2026-01-15T02:00:00Z", "UTC")).toMatch(/UTC/);
    expect(formatDateTime("2026-01-15T02:00:00Z", "Asia/Dubai")).toMatch(/06:00:00/);
    expect(formatDateTime("2026-01-15T02:00:00Z", "Asia/Dubai")).toMatch(/GMT\+4/);
    expect(formatDateTime(null)).toBe("—");
  });

  it("shows UTC hour buckets in the chosen zone", () => {
    expect(hourLabel("2026-01-15 02", "Asia/Dubai")).toBe("06:00");
    expect(hourLabel("2026-01-15 02", "UTC")).toBe("02:00");
  });
});
