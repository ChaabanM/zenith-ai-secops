import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Collapsible, SensorConfidence, shortId, ShortId } from "../evidence";

describe("evidence display helpers", () => {
  it("shortens only long ids and keeps the distinguishing tail and the full value", () => {
    expect(shortId("evt-1c5bb79ccfc9")).toBe("evt-1c5bb79ccfc9");
    const a = "evt-test-scenario-000123";
    const b = "evt-test-scenario-000124";
    expect(shortId(a)).not.toBe(shortId(b)); // shared prefixes stay distinguishable
    const { container } = render(<ShortId value={a} />);
    expect(container.firstChild).toHaveAttribute("title", a);
    expect(screen.getByText(a)).toHaveClass("sr-only"); // full id for assistive technology
  });

  it("shows missing sensor confidence as 'not reported', never as zero", () => {
    render(<><SensorConfidence value={null} /><SensorConfidence value={0} /><SensorConfidence value={0.94} /></>);
    const missing = screen.getByText("not reported");
    expect(missing).toHaveAttribute("title", "The sensor did not report a confidence value. This is not the same as low confidence.");
    expect(screen.getByText("0.00")).toBeInTheDocument();
    expect(screen.getByText("0.94")).toBeInTheDocument();
  });

  it("collapses on screen but keeps content in the printed page", async () => {
    render(<Collapsible label="More">detail text</Collapsible>);
    const content = screen.getByText("detail text");
    expect(content).toHaveClass("hidden", "print:block");
    await userEvent.setup().click(screen.getByRole("button", { name: "More" }));
    expect(content).not.toHaveClass("hidden");
    expect(screen.getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "true");
  });
});
