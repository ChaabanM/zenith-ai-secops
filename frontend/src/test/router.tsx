import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation, type Location } from "react-router-dom";

/** Renders routes in a MemoryRouter and exposes the current location for assertions. */
export function renderRoutes(routes: Record<string, ReactElement>, initial: string | Partial<Location>) {
  const probe: { location: Location | null } = { location: null };
  function Probe() {
    probe.location = useLocation();
    return null;
  }
  const ui = () => (
    <MemoryRouter initialEntries={[initial]}>
      <Probe />
      <Routes>
        {Object.entries(routes).map(([path, element]) => (
          <Route key={path} path={path} element={element} />
        ))}
      </Routes>
    </MemoryRouter>
  );
  const view = render(ui());
  return { probe, view, rerenderFresh: () => render(ui()) };
}
