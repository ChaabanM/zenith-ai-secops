import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { briefing, finding } from "@/test/briefingFixture";
import { eventDetail } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderRoutes } from "@/test/router";

import { IncidentDetailPage } from "../../incidents/IncidentDetailPage";
import { BriefingPage } from "../BriefingPage";

const PERIODS = { timezone: "UTC", shifts: [{ name: "day", label: "Day shift", start: "06:00" }, { name: "night", label: "Night shift", start: "22:00" }],
  data_start: "2026-01-12T00:00:00Z", data_end: "2026-01-15T00:00:00Z", max_period_days: 31, time_basis: "Periods select events by occurrence time." };
const FACETS = { total_events: 1, sources: [], severities: [], zones: [{ value: "Gate-A", count: 1 }], devices: [], occurred_min: null, occurred_max: null };
const QUALITY = { id: 31, issue_type: "device_offline", subtype: null, severity: "high", title: "radar-int05 offline", description: "Offline.", rule_id: "HLT-001",
  rule_version: "1", confidence: 0.95, occurred_from: "2026-01-15T12:46:40Z", occurred_to: "2026-01-15T12:46:40Z", time_basis: "occurred_at", event_count: 1,
  submission_count: 1, devices: [], zones: [], sources: [], status: "open", first_detected_at: "2026-01-15T13:00:00Z", last_detected_at: "2026-01-15T13:00:00Z",
  detection_reason: "Device reported offline.", uncertainty: null, missing_evidence: null, details: {}, evidence: [], evidence_total: 0, evidence_truncated: false };

function setup(routes: Record<string, unknown> = {}, path = "/briefings") {
  const api = mockApi({ "GET /briefing": briefing(), "GET /briefing/periods": PERIODS, "GET /events/facets": FACETS, "GET /events/7": eventDetail(7, "h1"),
    "GET /incidents": { items: [], page: 1, page_size: 10, total: 0, total_pages: 0, has_next: false }, "GET /quality-issues/31": QUALITY, ...routes });
  const { probe } = renderRoutes({ "/briefings": <BriefingPage />, "/incidents/:id": <IncidentDetailPage /> }, path);
  return { api, probe, user: userEvent.setup() };
}

describe("Briefing page", () => {
  it("shows the executive summary, its provenance, metrics, priority incidents and grounding status", async () => {
    setup();
    expect(await screen.findByTestId("executive-summary")).toHaveTextContent("Needs attention first: #5 radar-int05 offline");
    expect(screen.getByTestId("narrative-source")).toHaveTextContent("deterministic templates from database values (no language model)");
    const priority = screen.getByTestId("priority-incident");
    expect(within(priority).getByText("Priority 74.0")).toBeInTheDocument();
    expect(within(priority).getByRole("link", { name: "Incident #5" })).toHaveAttribute("href", "/incidents/5");
    expect(screen.getByTestId("grounding")).toHaveTextContent("All 2 source events, 1 incidents and 1 quality findings referenced in this briefing were verified");
    expect(screen.getByTestId("confidence")).toHaveTextContent("Overall confidence: medium (0.72)");
  });

  it("labels each finding by basis and shows empty sections truthfully", async () => {
    setup();
    await screen.findByTestId("executive-summary");
    const health = screen.getByTestId("section-device_health");
    expect(within(health).getByText("Reported by sensors")).toBeInTheDocument();
    expect(within(health).getByText(/Uncertain: Whether the device is working now is unknown/)).toBeInTheDocument();
    expect(screen.getByTestId("section-conflicts_uncertainty")).toHaveTextContent("No conflicts or uncertainty flags were found for this period.");
    expect(within(screen.getByTestId("recommendations")).getByText("Recommendation")).toBeInTheDocument();
    expect(screen.getByTestId("recommendations")).toHaveTextContent("Advisory only");
  });

  it("opens the source event (with its raw record) from a finding", async () => {
    const { probe, user } = setup();
    await user.click(within(await screen.findByTestId("section-device_health")).getByRole("button", { name: "Open source event h1" }));
    const dialog = await screen.findByRole("dialog", { name: "Event detail" });
    expect(await within(dialog).findByText(/ORIGINAL-RAW/)).toBeInTheDocument();
    expect(probe.location?.search).toContain("event=7");
  });

  it("opens the quality issue from a finding", async () => {
    const { user } = setup();
    await user.click(within(await screen.findByTestId("section-device_health")).getByRole("button", { name: "Quality #31" }));
    const dialog = await screen.findByRole("dialog", { name: "Quality issue detail" });
    expect(await within(dialog).findByText("Device reported offline.")).toBeInTheDocument();
  });

  it("links to the incident investigation with a way back to the briefing", async () => {
    const { probe, user } = setup({ "GET /incidents/5": () => ({ status: 404, body: { error: { code: "not_found", message: "x" } } }) }, "/briefings?mode=day&date=2026-01-15");
    await user.click(within(await screen.findByTestId("priority-incident")).getByRole("link", { name: "Incident #5" }));
    await waitFor(() => expect(probe.location?.pathname).toBe("/incidents/5"));
    expect(await screen.findByRole("link", { name: /back to briefing/i })).toHaveAttribute("href", "/briefings?mode=day&date=2026-01-15");
  });

  it("links 'all events' to the Events page with the finding's filters", async () => {
    setup();
    const link = await screen.findByRole("link", { name: /All 14 events/ });
    expect(link.getAttribute("href")).toContain("/events?device_id=acs-x");
    expect(link.getAttribute("href")).toContain("occurred_from=");
  });

  it("explains priority factors on request", async () => {
    const { user } = setup();
    await user.click(await screen.findByRole("button", { name: /why this priority/i }));
    expect(screen.getByText("Stored severity is critical.")).toBeInTheDocument();
  });

  it("sends the selected period, shift, time zone and zone to GET /briefing", async () => {
    const { api, user } = setup();
    await screen.findByTestId("executive-summary");
    await user.click(screen.getByRole("button", { name: "Daily" }));
    await user.type(screen.getByLabelText("Date"), "2026-01-14");
    await user.selectOptions(screen.getByLabelText("Time zone"), "Asia/Dubai");
    await user.selectOptions(screen.getByLabelText("Zone"), "Gate-A");
    await user.click(screen.getByRole("button", { name: /generate briefing/i }));
    await waitFor(() => expect(api.last("/briefing")!.url.searchParams.get("mode")).toBe("day"));
    const p = api.last("/briefing")!.url.searchParams;
    expect([p.get("date"), p.get("tz"), p.get("zone")]).toEqual(["2026-01-14", "Asia/Dubai", "Gate-A"]);
    await user.click(screen.getByRole("button", { name: "Shift" }));
    await user.selectOptions(screen.getByLabelText("Shift"), "night");
    await user.click(screen.getByRole("button", { name: /generate briefing/i }));
    await waitFor(() => expect(api.last("/briefing")!.url.searchParams.get("shift")).toBe("night"));
  });

  it("shows model provenance and fallback reasons", async () => {
    setup({ "GET /briefing": briefing({ executive_summary: { text: "Reworded.", generated_by: "deterministic_template", provider: "deterministic",
      validated: true, fallback_reason: "model output rejected by the grounding check: unknown event id evt-x" } }) });
    expect(await screen.findByTestId("narrative-source")).toHaveTextContent("Model output not used: model output rejected by the grounding check");
  });

  it("shows an empty period without filler", async () => {
    setup({ "GET /briefing": briefing({ priority_incidents: [], evidence_index: [], executive_summary: { text: "No events with an occurrence time in this period are stored. This is not confirmation that operations were normal.",
      generated_by: "deterministic_template", provider: "deterministic", validated: true, fallback_reason: null } }) });
    expect(await screen.findByTestId("no-priority")).toHaveTextContent("No unresolved incidents had activity in this period.");
    expect(screen.getByTestId("executive-summary")).toHaveTextContent("not confirmation that operations were normal");
  });

  it("shows loading, then API validation errors with retry", async () => {
    let fail = true;
    const { user } = setup({ "GET /briefing": () => (fail ? { status: 422, body: { error: { code: "invalid_parameter", message: "end must be after start" } } } : { body: briefing() }) });
    expect(screen.getByText(/generating briefing/i)).toBeInTheDocument();
    expect(await screen.findByText("end must be after start")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByTestId("executive-summary")).toBeInTheDocument();
  });

  it("keeps long sections collapsed on screen but complete in print", async () => {
    const many = Array.from({ length: 7 }, (_, i) => finding({ id: `data_quality-${i + 1}`, category: "data_quality", basis: "database_fact",
      statement: `Quality line ${i + 1}`, uncertainty: [], quality_issue_ids: [], incident_ids: [], evidence: [] }));
    const { user } = setup({ "GET /briefing": briefing({ sections: [{ key: "data_quality", title: "Data-quality issues", summary: "7 findings.", findings: many, empty_reason: null }] }) });
    const section = await screen.findByTestId("section-data_quality");
    expect(within(section).getByText("Quality line 4")).toBeInTheDocument();
    const more = within(section).getByTestId("more-findings");
    const hidden = more.querySelector("[data-collapsed]")!;
    expect(hidden).toHaveClass("hidden", "print:block");
    expect(within(hidden as HTMLElement).getAllByText(/Quality line [5-7]/)).toHaveLength(3); // rendered, so printed
    await user.click(within(more).getByRole("button", { name: "Show 3 more findings" }));
    expect(hidden).not.toHaveClass("hidden");
  });

  it("shows statement, basis and confidence by default with the reasoning one click away", async () => {
    const { user } = setup();
    const health = await screen.findByTestId("section-device_health");
    expect(within(health).getByText(/Confidence/)).toHaveTextContent("high");
    const reasoning = within(health).getByTestId("finding-reasoning");
    expect(reasoning.querySelector("[data-collapsed]")).toHaveClass("hidden", "print:block");
    await user.click(within(reasoning).getByRole("button", { name: /why the system says this/i }));
    expect(within(reasoning).getByText("Health status as reported by the monitoring system.")).toBeVisible();
    expect(within(health).getByText(/Uncertain: Whether the device is working now is unknown/)).toBeInTheDocument(); // never collapsed
  });

  it("simplifies the period banner and keeps boundaries and caveats available", async () => {
    setup();
    await screen.findByTestId("executive-summary");
    expect(screen.getByTestId("period-line")).not.toHaveTextContent("2026-01-15T06:00:00Z");
    const details = screen.getByTestId("period-details");
    expect(details).toHaveTextContent("[2026-01-15T06:00:00Z, 2026-01-15T14:00:00Z)");
    expect(details).toHaveTextContent("Incident status is the operator status now");
    expect(details).toHaveTextContent("Events that arrive later can change these counts.");
  });

  it("prints with the browser's print function", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    const { user } = setup();
    await screen.findByTestId("executive-summary");
    await user.click(screen.getByRole("button", { name: /print/i }));
    expect(print).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /print/i }).closest("div")).toHaveClass("print:hidden"); // controls are not printed
  });

  it("labels quality-rule findings and statistical comparisons as different measures", async () => {
    setup({ "GET /briefing": briefing({ sections: [{ key: "repeated_by_location", title: "Repeated events by location",
      summary: "0 quality-rule spike/repetition finding(s); 2 zones showed elevated activity in statistical comparisons with the same hours on previous days.",
      findings: [], empty_reason: "None." }] }) });
    const section = await screen.findByTestId("section-repeated_by_location");
    expect(section).toHaveTextContent("0 quality-rule spike/repetition finding(s); 2 zones showed elevated activity in statistical comparisons");
  });
});
