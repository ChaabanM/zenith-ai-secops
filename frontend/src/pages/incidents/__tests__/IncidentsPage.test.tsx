import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { mockApi } from "@/test/mockApi";
import { SUMMARY, summaryItem } from "@/test/fixtures";
import { renderRoutes } from "@/test/router";

import { IncidentsPage } from "../IncidentsPage";

const FACETS = { total_events: 10, sources: [], severities: [], zones: [{ value: "Gate-C", count: 3 }], devices: [{ value: "acs-gate-c", count: 3 }], occurred_min: null, occurred_max: null };

function page(items = [summaryItem(), summaryItem({ id: 12, title: "Speeding pass", severity: "high", status: "resolved", category: "traffic_enforcement", conflict_count: 0 })], extra = {}) {
  return { items, page: 1, page_size: 25, total: 51, total_pages: 3, has_next: true, ...extra };
}

function setup(path = "/incidents", routes: Record<string, unknown> = {}) {
  const api = mockApi({ "GET /incidents": page(), "GET /incidents/summary": SUMMARY, "GET /events/facets": FACETS, ...routes });
  const { probe } = renderRoutes({ "/incidents": <IncidentsPage />, "/incidents/:id": <p>detail page</p> }, path);
  return { api, probe, user: userEvent.setup() };
}

const lastParams = (api: ReturnType<typeof mockApi>) => api.last("/incidents")!.url.searchParams;

describe("Incident overview", () => {
  it("shows real summary counts from GET /incidents/summary", async () => {
    setup();
    const card = async (name: string) => within(await screen.findByTestId(`card-${name}`));
    expect((await card("Total incidents")).getByText("718")).toBeInTheDocument();
    expect((await card("Total incidents")).getByText("712 unresolved")).toBeInTheDocument();
    expect((await card("New / open")).getByText("700")).toBeInTheDocument();
    expect((await card("Critical")).getByText("81")).toBeInTheDocument();
    expect((await card("Critical")).getByText("79 unresolved")).toBeInTheDocument();
    expect((await card("High severity")).getByText("289")).toBeInTheDocument();
    expect((await card("Investigating")).getByText("7")).toBeInTheDocument();
    expect((await card("Resolved")).getByText("4")).toBeInTheDocument();
  });

  it("renders table rows with id, title, severity, confidence, status, zone and event count", async () => {
    setup();
    const rows = await screen.findAllByTestId("incident-row");
    expect(rows).toHaveLength(2);
    const first = within(rows[0]);
    expect(first.getByText("#706")).toBeInTheDocument();
    expect(first.getByText(/denied access attempts/)).toBeInTheDocument();
    expect(first.getByText("Critical")).toBeInTheDocument();
    expect(first.getByText("0.89")).toBeInTheDocument();
    expect(first.getByText("New / open")).toBeInTheDocument();
    expect(first.getByText("Gate-C")).toBeInTheDocument();
    expect(first.getByText("1 conflict")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Resolved")).toBeInTheDocument();
    expect(screen.getByTestId("result-count")).toHaveTextContent("51 incidents");
  });

  it("defaults to the priority sort and server-side pagination", async () => {
    const { api, user } = setup();
    await screen.findAllByTestId("incident-row");
    expect(lastParams(api).get("sort")).toBe("priority");
    expect(lastParams(api).get("page_size")).toBe("25");
    await user.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(lastParams(api).get("page")).toBe("2"));
    await user.selectOptions(screen.getByLabelText("Sort order"), "first_occurred_at");
    await waitFor(() => expect(lastParams(api).get("sort")).toBe("first_occurred_at"));
    expect(lastParams(api).get("page")).toBe("1"); // changing sort resets to page 1
  });

  it.each([
    ["Severity", "critical", "severity", "critical"],
    ["Status", "investigating", "status", "investigating"],
    ["Category", "traffic_enforcement", "category", "traffic_enforcement"],
    ["Source type", "anpr", "source_type", "anpr"],
    ["Zone / location", "Gate-C", "zone", "Gate-C"],
  ])("sends the %s filter to the backend", async (label, option, param, expected) => {
    const { api, user } = setup();
    await screen.findAllByTestId("incident-row");
    await waitFor(() => expect(screen.getByLabelText(label).querySelectorAll("option").length).toBeGreaterThan(1));
    await user.selectOptions(screen.getByLabelText(label), option);
    await waitFor(() => expect(lastParams(api).get(param)).toBe(expected));
  });

  it("sends device and time filters and clears them", async () => {
    const { api, user } = setup();
    await screen.findAllByTestId("incident-row");
    await user.type(screen.getByLabelText("Device ID"), "acs-gate-c{Enter}");
    await waitFor(() => expect(lastParams(api).get("device_id")).toBe("acs-gate-c"));
    const from = screen.getByLabelText("Active from");
    await user.type(from, "2026-01-14T10:00");
    await waitFor(() => expect(lastParams(api).get("occurred_from")).toMatch(/^2026-01-14T/));
    await user.click(screen.getByRole("button", { name: /clear filters/i }));
    await waitFor(() => {
      expect(lastParams(api).get("device_id")).toBeNull();
      expect(lastParams(api).get("occurred_from")).toBeNull();
    });
  });

  it("restores filters from the URL (navigating back keeps context)", async () => {
    const { api } = setup("/incidents?severity=high&status=open,acknowledged,investigating&page=2");
    await screen.findAllByTestId("incident-row");
    expect(lastParams(api).get("severity")).toBe("high");
    expect(lastParams(api).get("status")).toBe("open,acknowledged,investigating");
    expect(lastParams(api).get("page")).toBe("2");
    expect(screen.getByLabelText("Status")).toHaveValue("open,acknowledged,investigating");
  });

  it("opens the investigation with the list query remembered", async () => {
    const { probe, user } = setup("/incidents?severity=critical");
    await user.click((await screen.findAllByTestId("incident-row"))[0]);
    await waitFor(() => expect(probe.location?.pathname).toBe("/incidents/706"));
    expect(probe.location?.state).toEqual({ from: "?severity=critical" });
  });

  it("clicking a summary card filters the list", async () => {
    const { api, user } = setup();
    await user.click(await screen.findByTestId("card-Critical"));
    await waitFor(() => expect(lastParams(api).get("severity")).toBe("critical"));
  });

  it("shows loading, then the empty state", async () => {
    setup("/incidents", { "GET /incidents": page([], { total: 0, total_pages: 0, has_next: false }) });
    expect(screen.getByText(/loading incidents/i)).toBeInTheDocument();
    expect(await screen.findByText("No incidents yet")).toBeInTheDocument();
  });

  it("shows a filtered empty state with a clear action", async () => {
    setup("/incidents?zone=Nowhere", { "GET /incidents": page([], { total: 0, total_pages: 0, has_next: false }) });
    expect(await screen.findByText("No incidents match these filters")).toBeInTheDocument();
  });

  it("shows API failures and retries", async () => {
    let fail = true;
    const { user } = setup("/incidents", {
      "GET /incidents": () => (fail ? { status: 500, body: { error: { code: "internal", message: "database locked" } } } : { body: page() }),
    });
    expect(await screen.findByText("Could not load incidents")).toBeInTheDocument();
    expect(screen.getByText("database locked")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findAllByTestId("incident-row")).toHaveLength(2);
  });
});
