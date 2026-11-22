import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { eventDetail, incidentDetail } from "@/test/fixtures";
import { mockApi, type Call } from "@/test/mockApi";
import { renderRoutes } from "@/test/router";

import { IncidentDetailPage } from "../IncidentDetailPage";

function setup(patch: (body: { status: string; note: string | null }) => { status?: number; body: unknown }) {
  let stored = incidentDetail();
  const api = mockApi({
    "GET /incidents/706": () => ({ body: stored }),
    "GET /events/1": eventDetail(1, "evt-a"),
    "PATCH /incidents/706": (call: Call) => {
      const result = patch(call.body as { status: string; note: string | null });
      if (!result.status || result.status < 400) stored = result.body as typeof stored;
      return result;
    },
  });
  const { view, rerenderFresh } = renderRoutes({ "/incidents/:id": <IncidentDetailPage /> }, "/incidents/706");
  return { api, user: userEvent.setup(), view, rerenderFresh };
}

const ok = (body: { status: string; note: string | null }) => ({
  body: incidentDetail({ status: body.status as never, status_note: body.note, status_updated_at: "2026-11-22T14:00:00Z" }),
});

describe("Operator status management", () => {
  it("requires confirmation showing incident id, current and proposed status", async () => {
    const { api, user } = setup(ok);
    await user.click(await screen.findByRole("button", { name: "Acknowledge" }));
    const dialog = screen.getByRole("dialog", { name: /change status of incident #706/i });
    expect(within(dialog).getByText("#706")).toBeInTheDocument();
    expect(within(dialog).getByText("New / open")).toBeInTheDocument();
    expect(within(dialog).getByText("Acknowledged")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "PATCH")).toBe(false); // nothing sent before confirmation
  });

  it("cancelling sends nothing and keeps the status", async () => {
    const { api, user } = setup(ok);
    await user.click(await screen.findByRole("button", { name: "Resolve" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "PATCH")).toBe(false);
    expect(screen.getAllByText("New / open").length).toBeGreaterThan(0);
  });

  it("sends status and note after confirmation, shows success and persists across reload", async () => {
    const { api, user, view, rerenderFresh } = setup(ok);
    await user.click(await screen.findByRole("button", { name: "Mark false positive" }));
    await user.type(screen.getByPlaceholderText(/reason or context/i), "Test alarm by maintenance");
    await user.click(screen.getByRole("button", { name: /confirm: false positive/i }));
    expect(await screen.findByRole("status")).toHaveTextContent("Status saved: False positive.");
    expect(api.last("/incidents/706")).toBeDefined();
    const patch = api.calls.find((c) => c.method === "PATCH")!;
    expect(patch.body).toEqual({ status: "false_positive", note: "Test alarm by maintenance" });
    expect(screen.getByText("Test alarm by maintenance")).toBeInTheDocument();
    // simulated browser refresh: fresh render loads the persisted status from the backend
    view.unmount();
    rerenderFresh();
    await waitFor(() => expect(screen.getAllByText("False positive").length).toBeGreaterThan(0));
    expect(screen.getByText("Test alarm by maintenance")).toBeInTheDocument();
  });

  it("does not show success when the backend rejects the update", async () => {
    const { user } = setup(() => ({ status: 500, body: { error: { code: "internal", message: "write failed" } } }));
    await user.click(await screen.findByRole("button", { name: "Start investigating" }));
    await user.click(screen.getByRole("button", { name: /confirm: investigating/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: write failed");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // still open so the operator can retry or cancel
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getAllByText("New / open").length).toBeGreaterThan(0);
  });
});
