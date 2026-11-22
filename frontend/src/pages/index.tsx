import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

export { DashboardPage } from "./dashboard/DashboardPage";
export { EventsPage } from "./events/EventsPage";
export { BriefingPage as BriefingsPage } from "./briefing/BriefingPage";
export { DataQualityPage } from "./quality/DataQualityPage";
export { IncidentDetailPage } from "./incidents/IncidentDetailPage";
export { IncidentsPage } from "./incidents/IncidentsPage";

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md space-y-4 pt-16 text-center">
      <h2 className="text-2xl font-semibold">Page not found</h2>
      <p className="text-muted-foreground">The page you are looking for does not exist.</p>
      <Button asChild>
        <Link to="/">Back to Dashboard</Link>
      </Button>
    </div>
  );
}
