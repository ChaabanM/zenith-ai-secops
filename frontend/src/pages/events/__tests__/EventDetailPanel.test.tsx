import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { eventDetail, summaryItem } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";

import { EventDetailPanel } from "../EventDetailPanel";

describe("Event → related incident", () => {
  it("links an event back to the incidents that use it", async () => {
    const api = mockApi({
      "GET /events/5": eventDetail(5, "evt-x"),
      "GET /incidents": { items: [summaryItem({ id: 42, title: "Door forced" })], page: 1, page_size: 10, total: 1, total_pages: 1, has_next: false },
    });
    render(<MemoryRouter><EventDetailPanel eventId={5} onClose={() => undefined} /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: /#42 Door forced/ })).toHaveAttribute("href", "/incidents/42");
    expect(api.last("/incidents")!.url.searchParams.get("event")).toBe("5");
  });

  it("says so truthfully when the event is in no incident", async () => {
    mockApi({ "GET /events/5": eventDetail(5, "evt-x"), "GET /incidents": { items: [], page: 1, page_size: 10, total: 0, total_pages: 0, has_next: false } });
    render(<MemoryRouter><EventDetailPanel eventId={5} onClose={() => undefined} /></MemoryRouter>);
    expect(await screen.findByTestId("no-related-incidents")).toHaveTextContent("not part of any active incident");
  });
});
