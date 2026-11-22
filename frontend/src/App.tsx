import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { AppLayout } from "@/layouts/AppLayout";
import {
  BriefingsPage,
  DashboardPage,
  DataQualityPage,
  EventsPage,
  IncidentDetailPage,
  IncidentsPage,
  NotFoundPage,
} from "@/pages";

const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "events", element: <EventsPage /> },
      { path: "incidents", element: <IncidentsPage /> },
      { path: "incidents/:id", element: <IncidentDetailPage /> },
      { path: "data-quality", element: <DataQualityPage /> },
      { path: "briefings", element: <BriefingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
