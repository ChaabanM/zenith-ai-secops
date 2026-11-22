import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { eventDetail, incidentDetail } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderRoutes } from "@/test/router";

import { IncidentDetailPage } from "../IncidentDetailPage";
import { sortTimeline } from "../IncidentTimeline";

const QUALITY_ISSUE = {
  id: 4107, issue_type: "conflicting_sensor_results", subtype: "identity", severity: "high", title: "Badge of EMP-1 used while face recognition matched EMP-2",
  description: "Two sensors disagree.", rule_id: "SNS-001", rule_version: "1", confidence: 0.9, occurred_from: "2026-01-14T22:56:06Z",
  occurred_to: "2026-01-14T22:57:00Z", time_basis: "occurred_at", event_count: 2, submission_count: 2, devices: [], zones: [], sources: [],
  status: "open", first_detected_at: "2026-01-14T23:00:00Z", last_detected_at: "2026-01-14T23:00:00Z", detection_reason: "Identity mismatch.",
  uncertainty: null, missing_evidence: null, details: {}, evidence_total: 1, evidence_truncated: false,
  evidence: [{ role: "badge_use", external_event_id: "evt-a", event: { id: 1, event_id: "evt-a", source_type: "access_control", device_id: "acs-gate-c", event_type: "access_granted", occurred_at: "2026-01-14T22:56:06Z", received_at: null, zone: "Gate-C", latitude: null, longitude: null, severity: "low", confidence: null }, submission: null }],
};

function setup(routes: Record<string, unknown> = {}, path = "/incidents/706") {
  const api = mockApi({
    "GET /incidents/706": incidentDetail(),
    "GET /events/1": eventDetail(1, "evt-a"),
    "GET /events/3": eventDetail(3, "evt-face"),
    "GET /incidents": { items: [incidentDetail()], page: 1, page_size: 10, total: 1, total_pages: 1, has_next: false },
    "GET /quality-issues/4107": QUALITY_ISSUE,
    ...routes,
  });
  const { probe } = renderRoutes(
    { "/incidents/:id": <IncidentDetailPage />, "/incidents": <p>list</p> },
    { pathname: path, search: "", state: { from: "?severity=critical" } },
  );
  return { api, probe, user: userEvent.setup() };
}

describe("Incident investigation view", () => {
  it("renders every Section 4.2 field from the backend", async () => {
    setup();
    expect(await screen.findByTestId("incident-id")).toHaveTextContent("Incident #706");
    expect(screen.getByRole("heading", { level: 2, name: /denied access attempts/ })).toBeInTheDocument();
    expect(screen.getAllByText("Unauthorized access").length).toBeGreaterThan(0);
    expect(screen.getByTestId("severity-reason")).toHaveTextContent("Highest severity among strongly linked evidence");
    expect(screen.getByTestId("confidence-reason")).toHaveTextContent("Heuristic score (not a probability)");
    expect(screen.getAllByText("0.89").length).toBeGreaterThan(0);
    expect(screen.getAllByText("high (heuristic)").length).toBeGreaterThan(0);
    expect(screen.getByText(/acs-gate-c, frs-gate-c/)).toBeInTheDocument();
    expect(screen.getByTestId("linkage-explanation")).toHaveTextContent("same access credential");
    expect(within(screen.getByTestId("evidence-summary")).getByText("Linked source events")).toBeInTheDocument();
  });

  it("orders the timeline by occurred_at with a stable tie-breaker, not arrival order", async () => {
    setup();
    await screen.findByTestId("incident-id");
    const ids = screen.getAllByTestId("timeline-entry").map((li) => li.getAttribute("data-event-id"));
    expect(ids).toEqual(["evt-a", "evt-b", "evt-face", "evt-cam"]);
    const tied = sortTimeline([
      { ...incidentDetail().timeline[1], event_pk: 9 },
      { ...incidentDetail().timeline[1], event_pk: 5 },
    ]);
    expect(tied.map((t) => t.event_pk)).toEqual([5, 9]);
  });

  it("classifies evidence: conflicting, uncertain (low sensor confidence), strong and contextual", async () => {
    setup();
    await screen.findByTestId("incident-id");
    const cls = Object.fromEntries(screen.getAllByTestId("timeline-entry").map((li) => [li.getAttribute("data-event-id"), li.getAttribute("data-evidence-class")]));
    expect(cls).toEqual({ "evt-a": "strong", "evt-b": "strong", "evt-face": "conflict", "evt-cam": "uncertain" });
    const cam = screen.getAllByTestId("timeline-entry").find((li) => li.getAttribute("data-event-id") === "evt-cam")!;
    expect(within(cam).getByText(/0\.42 \(below 0\.60\)/)).toBeInTheDocument();
    expect(within(cam).getByText(/not verified identity evidence/)).toBeInTheDocument();
    const face = screen.getAllByTestId("timeline-entry").find((li) => li.getAttribute("data-event-id") === "evt-face")!;
    expect(within(face).getByText("Time and place pairing only (no shared identifier)")).toBeInTheDocument();
  });

  it("preserves original event ids and shows linkage explanations per entry", async () => {
    setup();
    await screen.findByTestId("incident-id");
    for (const id of ["evt-a", "evt-b", "evt-face", "evt-cam"]) expect(screen.getByRole("button", { name: `Open source event ${id}` })).toHaveTextContent(id);
    expect(screen.getByText("The only face capture at Gate-C within 3 s of the only badge use.")).toBeInTheDocument();
  });

  it("opens the source event with its original raw submission and related incident", async () => {
    const { probe, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Open source event evt-a" }));
    const dialog = await screen.findByRole("dialog", { name: "Event detail" });
    expect(await within(dialog).findByText(/ORIGINAL-RAW/)).toBeInTheDocument();
    expect(within(dialog).getByText("#11")).toBeInTheDocument(); // submission id
    expect(await within(dialog).findByText("(this incident)")).toBeInTheDocument();
    expect(probe.location?.search).toBe("?event=1");
    expect(probe.location?.state).toEqual({ from: "?severity=critical" }); // back-navigation context kept
  });

  it("opens a related quality issue and its supporting event link", async () => {
    const { user } = setup();
    await screen.findByTestId("incident-id");
    await user.click(within(screen.getByTestId("quality-issues")).getByRole("button", { name: /#4107/ }));
    const dialog = await screen.findByRole("dialog", { name: "Quality issue detail" });
    expect(await within(dialog).findByText("Identity mismatch.")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /open event/i })).toHaveAttribute("href", "/events?event=1");
  });

  it("shows facts, inferences, conflicts side by side, missing information and uncertainty", async () => {
    const { user } = setup();
    await screen.findByTestId("incident-id");
    expect(screen.getByTestId("observations")).toHaveTextContent("acs-gate-c reported access denied");
    expect(screen.getByTestId("inferences")).toHaveTextContent("not to a verified person");
    const conflicts = screen.getByTestId("conflicts");
    expect(conflicts).toHaveTextContent("badge holder EMP-1 vs face match EMP-2");
    const group = within(conflicts).getByTestId("conflict-group");
    expect(within(group).getByRole("button", { name: "evt-a" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "evt-face" })).toBeInTheDocument();
    expect(group).toHaveTextContent("does not decide which record is correct");
    await user.click(within(group).getByRole("button", { name: /compare reported values/i }));
    expect(await within(group).findByText("Credential id")).toBeInTheDocument();
    expect(screen.getByTestId("missing")).toHaveTextContent("Which identity is correct cannot be determined");
    expect(screen.getByTestId("uncertainty")).toHaveTextContent("linked by context only");
  });

  it("shows truthful empty states when the backend reports no conflicts or missing information", async () => {
    setup({ "GET /incidents/706": incidentDetail({ conflicts: [], missing_information: [], uncertainty: [], quality_issues: [], recommended_actions: [] }) });
    await screen.findByTestId("incident-id");
    const conflicts = screen.getByTestId("conflicts");
    expect(conflicts).toHaveAttribute("data-state", "none");
    expect(conflicts).toHaveTextContent("No conflicts reported by the correlation engine. This does not prove that every observation is correct.");
    expect(within(conflicts).queryByRole("img", { hidden: true })).toBeNull(); // no warning icon when nothing conflicts
    expect(screen.getByTestId("missing")).toHaveTextContent("No missing information was reported.");
    expect(screen.getByText("No recommendations were produced for this incident.")).toBeInTheDocument();
  });

  it("lists recommended actions labelled as advisory and not performed", async () => {
    setup();
    await screen.findByTestId("incident-id");
    const recs = screen.getByTestId("recommendations");
    expect(within(recs).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Recommendations only. Nothing below has been performed or triggered.")).toBeInTheDocument();
  });

  it("handles a missing incident truthfully", async () => {
    setup({}, "/incidents/999");
    expect(await screen.findByText("Incident #999 was not found")).toBeInTheDocument();
  });

  it("keeps the list query on the back link", async () => {
    setup();
    await screen.findByTestId("incident-id");
    expect(screen.getByRole("link", { name: /back to incidents/i })).toHaveAttribute("href", "/incidents?severity=critical");
  });

  it("shows a load error with retry", async () => {
    let fail = true;
    const { user } = setup({
      "GET /incidents/706": () => (fail ? { status: 503, body: { error: { code: "unavailable", message: "backend down" } } } : { body: incidentDetail() }),
    });
    expect(await screen.findByText("Could not load the incident")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(screen.getByTestId("incident-id")).toBeInTheDocument());
  });

  it("styles reported conflicts as a warning with the contradiction and its evidence", async () => {
    setup();
    const conflicts = await screen.findByTestId("conflicts");
    expect(conflicts).toHaveAttribute("data-state", "conflicts");
    expect(conflicts).toHaveTextContent("Conflicting evidence (1)");
    expect(conflicts).toHaveTextContent("badge holder EMP-1 vs face match EMP-2");
    expect(within(conflicts).getByRole("button", { name: "evt-face" })).toBeInTheDocument();
  });

  it("explains contextual versus uncertain evidence without changing the counts", async () => {
    setup();
    const kinds = await screen.findByTestId("evidence-kinds");
    expect(kinds).toHaveTextContent("rather than a strong identifier. It is never identity evidence and may be unrelated.");
    expect(kinds).toHaveTextContent("sensor-reported confidence below 0.60");
    const summary = screen.getByTestId("evidence-summary");
    expect(within(summary).getByText("Contextual (supporting only)").previousSibling).toHaveTextContent("1");
    expect(within(summary).getByText("Uncertain entries").previousSibling).toHaveTextContent("1");
  });

  it("keeps the methodology and record details collapsed but available, and the key facts visible", async () => {
    const { user } = setup();
    await screen.findByTestId("incident-id");
    expect(screen.getByTestId("incident-location")).toHaveTextContent("Gate-C");
    const method = screen.getByTestId("link-methodology");
    const content = method.querySelector("[data-collapsed]")!;
    expect(content).toHaveAttribute("data-collapsed", "true");
    expect(content).toHaveClass("hidden", "print:block"); // hidden on screen, always printed
    await user.click(within(method).getByRole("button", { name: /how each link was established/i }));
    expect(content).toHaveAttribute("data-collapsed", "false");
    expect(within(method).getByText("Shared credential")).toBeVisible();
    expect(screen.getByTestId("missing")).toBeVisible(); // missing information is never collapsed
    expect(screen.getByTestId("uncertainty")).toBeVisible();
  });
});
