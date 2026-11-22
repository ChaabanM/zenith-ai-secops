import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { dashboard } from "@/test/dashboardFixture";
import { eventDetail } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderRoutes } from "@/test/router";

import { EventsPage } from "../../events/EventsPage";
import { IncidentDetailPage } from "../../incidents/IncidentDetailPage";
import { DashboardPage } from "../DashboardPage";

const FACETS = { total_events: 1, sources: [], severities: [], zones: [{ value: "Gate-A", count: 1 }], devices: [], occurred_min: null, occurred_max: null };
const QUALITY = { id: 31, issue_type: "device_offline", subtype: null, severity: "high", title: "radar-int05 offline", description: "Offline.", rule_id: "HLT-001",
  rule_version: "1", confidence: 0.95, occurred_from: "2026-01-14T12:00:00Z", occurred_to: "2026-01-14T12:00:00Z", time_basis: "occurred_at", event_count: 1,
  submission_count: 1, devices: [], zones: [], sources: [], status: "open", first_detected_at: "2026-01-14T13:00:00Z", last_detected_at: "2026-01-14T13:00:00Z",
  detection_reason: "Device reported offline.", uncertainty: null, missing_evidence: null, details: {}, evidence_total: 1, evidence_truncated: false,
  evidence: [{ role: "offline_report", external_event_id: "h1", event: { id: 7, event_id: "h1", source_type: "device_health", device_id: "nms-01", event_type: "device_offline",
    occurred_at: "2026-01-14T12:00:00Z", received_at: null, zone: null, latitude: null, longitude: null, severity: "high", confidence: null }, submission: null }] };
const EMPTY_PAGE = { items: [], page: 1, page_size: 10, total: 0, total_pages: 0, has_next: false };

function setup(routes: Record<string, unknown> = {}, path = "/") {
  const api = mockApi({ "GET /dashboard/summary": dashboard(), "GET /events/facets": FACETS, "GET /quality-issues/31": QUALITY, "GET /events/7": eventDetail(7, "h1"),
    "GET /incidents": EMPTY_PAGE, "GET /events": EMPTY_PAGE, ...routes });
  const { probe } = renderRoutes({ "/": <DashboardPage />, "/incidents/:id": <IncidentDetailPage />, "/events": <EventsPage /> }, path);
  return { api, probe, user: userEvent.setup() };
}

describe("Operations overview", () => {
  it("shows the period with its time zone and the real period metrics", async () => {
    setup();
    expect(await screen.findByTestId("period")).toHaveTextContent("Evening shift, 14 Jan 2026 14:00 – 14 Jan 2026 22:00 (Asia/Dubai)");
    expect(screen.getByTestId("period")).toHaveTextContent("GMT+4");
    expect(screen.getByTestId("m-events")).toHaveTextContent("4,625");
    expect(screen.getByTestId("m-incidents")).toHaveTextContent("70");
    expect(screen.getByTestId("m-unresolved")).toHaveTextContent("68");
    expect(screen.getByTestId("m-high")).toHaveTextContent("41");
    expect(screen.getByTestId("m-high")).toHaveTextContent("40 unresolved");
    expect(screen.getByTestId("m-quality")).toHaveTextContent("539");
    expect(screen.getByTestId("m-health")).toHaveTextContent("2");
  });

  it("labels all-time totals separately from period figures", async () => {
    setup();
    const totals = await screen.findByTestId("stored-totals");
    expect(totals).toHaveTextContent("All stored data (ignores period and zone): 47,500 events");
    expect(totals).toHaveTextContent("1,500 invalid and 1,000 duplicate submissions");
  });

  it("links every metric to the matching filtered list for the same period", async () => {
    setup();
    const href = async (id: string) => decodeURIComponent((await screen.findByTestId(id)).getAttribute("href") ?? "");
    expect(await href("m-events")).toBe("/events?occurred_from=2026-01-14T10:00:00Z&occurred_to=2026-01-14T18:00:00Z");
    expect(await href("m-unresolved")).toContain("/incidents?occurred_from=2026-01-14T10:00:00Z&occurred_to=2026-01-14T18:00:00Z&status=open,acknowledged,investigating");
    expect(await href("m-high")).toContain("severity=critical,high");
    expect(await href("m-quality")).toBe("/data-quality?occurred_from=2026-01-14T10:00:00Z&occurred_to=2026-01-14T18:00:00Z");
  });

  it("lists priority incidents in the API order and opens the investigation with a way back", async () => {
    const { probe, user } = setup({ "GET /incidents/5": () => ({ status: 404, body: { error: { code: "not_found", message: "x" } } }) });
    const items = await screen.findAllByTestId("priority-incident");
    expect(items).toHaveLength(1);
    expect(within(items[0]).getByText("Critical")).toBeInTheDocument();
    expect(within(items[0]).getByText("0.80")).toBeInTheDocument();
    expect(within(items[0]).getByText("Harbour-Rd-INT01")).toBeInTheDocument();
    await user.click(within(items[0]).getByRole("link", { name: "Incident #5" }));
    await waitFor(() => expect(probe.location?.pathname).toBe("/incidents/5"));
    expect(await screen.findByRole("link", { name: /back to overview/i })).toHaveAttribute("href", "/");
  });

  it("opens a quality issue and its source event from the overview", async () => {
    const { user } = setup();
    await user.click(within(await screen.findByTestId("quality-high")).getByRole("button", { name: /#31 radar-int05 offline/ }));
    const dialog = await screen.findByRole("dialog", { name: "Quality issue detail" });
    expect(await within(dialog).findByRole("link", { name: /open event/i })).toHaveAttribute("href", "/events?event=7");
  });

  it("opens evidence events from a priority incident", async () => {
    const { user } = setup();
    await user.click(within((await screen.findAllByTestId("priority-incident"))[0]).getByRole("button", { name: "Open source event h1" }));
    expect(await within(await screen.findByRole("dialog", { name: "Event detail" })).findByText(/ORIGINAL-RAW/)).toBeInTheDocument();
  });

  it("previews the briefing and links to exactly the same period", async () => {
    setup();
    const preview = await screen.findByTestId("briefing-preview");
    expect(within(preview).getByTestId("preview-summary")).toHaveTextContent("Needs attention first: #5");
    expect(within(preview).getByText(/no language model/)).toBeInTheDocument();
    const link = within(preview).getByRole("link", { name: /open full briefing/i });
    expect(link).toHaveAttribute("href", "/briefings?mode=shift&date=2026-01-14&shift=evening&tz=Asia%2FDubai");
  });

  it("shows event activity in the period's time zone and reported spikes", async () => {
    setup();
    const activity = await screen.findByTestId("activity");
    expect(within(activity).getByText("CCTV")).toBeInTheDocument();
    expect(within(activity).getByText(/Volume per hour \(Asia\/Dubai\)/)).toBeInTheDocument();
    expect(within(activity).getByText(/14:00 – 15:00/)).toBeInTheDocument(); // 10:00Z and 11:00Z in Dubai
    expect(within(activity).getByRole("button", { name: "Quality #44" })).toBeInTheDocument();
  });

  it("never presents device health as confirmed healthy", async () => {
    setup({ "GET /dashboard/summary": dashboard({ device_health: [] }) });
    const panel = await screen.findByTestId("device-health");
    expect(panel).toHaveTextContent("no reports does not mean a device is healthy");
    expect(within(panel).getByTestId("no-health")).toHaveTextContent("No device-health problems were reported in this period");
    expect(panel).not.toHaveTextContent(/all devices|devices are (online|healthy|working)|status: (online|healthy)/i);
  });

  it("sends period, time zone and zone selections to the API", async () => {
    const { api, user } = setup();
    await screen.findByTestId("period");
    await user.selectOptions(screen.getByLabelText("Period"), "day");
    await waitFor(() => expect(api.last("/dashboard/summary")!.url.searchParams.get("mode")).toBe("day"));
    await user.selectOptions(screen.getByLabelText("Time zone"), "Asia/Dubai");
    await waitFor(() => expect(api.last("/dashboard/summary")!.url.searchParams.get("tz")).toBe("Asia/Dubai"));
    await waitFor(() => expect(screen.getByLabelText("Zone").querySelectorAll("option").length).toBe(2));
    await user.selectOptions(screen.getByLabelText("Zone"), "Gate-A");
    await waitFor(() => expect(api.last("/dashboard/summary")!.url.searchParams.get("zone")).toBe("Gate-A"));
  });

  it("guides a new reviewer when the database is empty", async () => {
    const { probe, user } = setup({ "GET /dashboard/summary": dashboard({ stored_totals: { ...dashboard().stored_totals, events_stored: 0 } }) });
    const card = await screen.findByTestId("getting-started");
    expect(card).toHaveTextContent("not a sign that operations are normal");
    expect(card).toHaveTextContent("scripts/load_demo.py");
    await user.click(within(card).getAllByRole("link", { name: /import events/i })[0]);
    await waitFor(() => expect(probe.location?.pathname).toBe("/events"));
    expect(await screen.findByText(/Choose file/i)).toBeInTheDocument(); // the import panel is open
  });

  it("shows loading, then an API error with retry", async () => {
    let fail = true;
    const { user } = setup({ "GET /dashboard/summary": () => (fail ? { status: 500, body: { error: { code: "internal_error", message: "database locked" } } } : { body: dashboard() }) });
    expect(screen.getByRole("status")).toHaveTextContent("Loading overview");
    expect(await screen.findByText("database locked")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByTestId("period")).toBeInTheDocument();
  });

  it("flags unverified references instead of hiding them", async () => {
    setup({ "GET /dashboard/summary": dashboard({ grounding: { ...dashboard().grounding, all_references_verified: false, unverified_references: ["event 9 (evt-x)"] } }) });
    expect(await screen.findByTestId("grounding")).toHaveTextContent("Unverified references: event 9 (evt-x)");
  });
});
