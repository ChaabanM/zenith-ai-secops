import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { type ConnectionState, useBackendHealth } from "@/hooks/useBackendHealth";
import { cn } from "@/lib/utils";

const LABELS: Record<ConnectionState, string> = {
  checking: "Checking backend…",
  online: "Backend connected",
  degraded: "Backend degraded",
  offline: "Backend offline",
};

const DOT: Record<ConnectionState, string> = {
  checking: "bg-neutral-400 animate-pulse",
  online: "bg-emerald-500",
  degraded: "bg-amber-500",
  offline: "bg-red-500",
};

export function BackendStatus() {
  const { state, health, error, lastChecked, refresh } = useBackendHealth();

  const title = [
    health ? `${health.app} v${health.version} (${health.environment}), database ${health.database}` : null,
    error ? `Error: ${error}` : null,
    lastChecked ? `Last checked ${lastChecked.toLocaleTimeString()}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div className="flex items-center gap-2 text-sm" title={title} data-testid="backend-status" data-state={state}>
      <span className={cn("size-2.5 rounded-full", DOT[state])} aria-hidden />
      <span className="text-muted-foreground">{LABELS[state]}</span>
      <Button variant="ghost" size="icon" className="size-7" onClick={refresh} aria-label="Re-check backend">
        <RefreshCw className="size-3.5" />
      </Button>
    </div>
  );
}
