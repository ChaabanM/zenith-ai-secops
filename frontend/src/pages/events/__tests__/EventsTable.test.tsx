import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { EventSummary } from "@/types/events";

import { EventsTable } from "../EventsTable";

const base: EventSummary = {
  id: 1, event_id: "evt-1c5bb79ccfc9", source_type: "access_control", device_id: "acs-gate-c", event_type: "access_denied",
  occurred_at: "2026-01-15T10:00:00Z", received_at: "2026-01-15T10:00:01Z", ingested_at: "2026-01-15T10:00:02Z", delay_seconds: 1,
  location: { latitude: null, longitude: null, zone: "Gate-C" }, severity: "low", confidence: null,
};

describe("Events table", () => {
  it("distinguishes 'not reported' confidence from low confidence and keeps reported values", () => {
    render(<EventsTable events={[base, { ...base, id: 2, event_id: "evt-low", confidence: 0.31 }]} selectedId={null} onSelect={() => undefined} />);
    const rows = screen.getAllByRole("row").slice(1);
    const missing = within(rows[0]).getByText("not reported");
    expect(missing).toHaveAttribute("title", expect.stringContaining("not the same as low confidence"));
    expect(within(rows[1]).getByText("0.31")).toBeInTheDocument();
    expect(within(rows[1]).getByText("(low)")).toHaveClass("sr-only"); // not colour alone
    expect(screen.getByRole("columnheader", { name: /sensor confidence/i })).toHaveAttribute("title", expect.stringContaining("not the same as low confidence"));
  });

  it("shows the original event id, shortening only long ids, and keeps rows clickable", async () => {
    const onSelect = vi.fn();
    const long = "evt-test-scenario-000123";
    render(<EventsTable events={[base, { ...base, id: 3, event_id: long }]} selectedId={null} onSelect={onSelect} />);
    expect(screen.getByText("evt-1c5bb79ccfc9")).toBeInTheDocument();
    const shortened = screen.getByTitle(long);
    expect(shortened).toHaveTextContent("evt-test-…-000123");
    await userEvent.setup().click(screen.getAllByRole("row")[2]);
    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it("scrolls inside its own container instead of widening the page", () => {
    render(<EventsTable events={[base]} selectedId={null} onSelect={() => undefined} />);
    const scroller = screen.getByTestId("events-scroll");
    expect(scroller).toHaveClass("overflow-x-auto", "relative"); // relative: screen-reader-only text stays inside the scroller
    expect(within(scroller).getByRole("table")).toHaveClass("min-w-[60rem]");
    expect(screen.getByTestId("scroll-hint")).toHaveClass("md:hidden");
  });
});
