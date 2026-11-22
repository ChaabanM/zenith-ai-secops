import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { mockApi } from "@/test/mockApi";

import { QualityIssueDetailPanel } from "../QualityIssueDetailPanel";

const REASON =
  "At Gate-A, credential CRD-1 (holder EMP-1) was granted at 10:00:00 and frs-gate-a matched EMP-2 with score 0.93 1 s apart. " +
  "These observations were provisionally paired for conflict analysis because they were the only badge and face-recognition events " +
  "within the time window. It is not verified that they concern the same person.";

const ISSUE = {
  id: 7, issue_type: "conflicting_sensor_results", subtype: "identity", severity: "high", title: "Badge of EMP-1 used while face recognition matched EMP-2 at Gate-A",
  description: "Neither record is assumed to be correct.", rule_id: "SNS-001", rule_version: "1", confidence: 0.9, occurred_from: "2026-01-15T10:00:00Z",
  occurred_to: "2026-01-15T10:00:01Z", time_basis: "occurred_at", event_count: 2, submission_count: 2, devices: [], zones: ["Gate-A"], sources: [], status: "open",
  first_detected_at: "2026-01-15T11:00:00Z", last_detected_at: "2026-01-15T11:00:00Z", detection_reason: REASON,
  uncertainty: "Pairing relies on time and place only (no shared identifier).", missing_evidence: null, details: {}, evidence_total: 2, evidence_truncated: false,
  evidence: [{ role: "badge_use", external_event_id: "b1", submission: null, event: { id: 1, event_id: "b1", source_type: "access_control", device_id: "acs-gate-a",
    event_type: "access_granted", occurred_at: "2026-01-15T10:00:00Z", received_at: null, zone: "Gate-A", latitude: null, longitude: null, severity: "low", confidence: null } }],
};

describe("Quality issue detail", () => {
  it("shows the rule score as a heuristic score, never as a percentage", async () => {
    mockApi({ "GET /quality-issues/7": ISSUE });
    render(<MemoryRouter><QualityIssueDetailPanel issueId={7} onClose={() => undefined} /></MemoryRouter>);
    const score = await screen.findByTestId("rule-score");
    expect(score).toHaveTextContent("Rule confidence score: 0.90 (heuristic)");
    expect(score).toHaveTextContent("A deterministic rule score, not a calibrated probability that the finding is correct.");
    expect(screen.queryByText(/rule certainty/i)).toBeNull();
    expect(screen.queryByText(/90%/)).toBeNull();
  });

  it("presents identity pairing as provisional, keeping the evidence", async () => {
    mockApi({ "GET /quality-issues/7": ISSUE });
    render(<MemoryRouter><QualityIssueDetailPanel issueId={7} onClose={() => undefined} /></MemoryRouter>);
    expect(await screen.findByText(/provisionally paired for conflict analysis/)).toHaveTextContent("It is not verified that they concern the same person.");
    expect(screen.queryByText(/treated as the same person/)).toBeNull();
    expect(within(screen.getByRole("dialog")).getByText("b1")).toBeInTheDocument();
    expect(screen.getByText(/sensor confidence not reported/)).toBeInTheDocument(); // missing confidence is not shown as zero
  });
});
