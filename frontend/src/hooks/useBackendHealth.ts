import { useCallback, useEffect, useState } from "react";

import { api } from "@/services/api";
import type { HealthResponse } from "@/types/health";

export type ConnectionState = "checking" | "online" | "degraded" | "offline";

interface BackendHealth {
  state: ConnectionState;
  health: HealthResponse | null;
  error: string | null;
  lastChecked: Date | null;
  refresh: () => void;
}

const POLL_INTERVAL_MS = 15_000;

export function useBackendHealth(): BackendHealth {
  const [state, setState] = useState<ConnectionState>("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    api
      .health(controller.signal)
      .then((result) => {
        setHealth(result);
        setError(null);
        setState(result.status === "ok" ? "online" : "degraded");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setHealth(null);
        setError(err instanceof Error ? err.message : "Unknown error");
        setState("offline");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLastChecked(new Date());
      });

    const timer = window.setTimeout(refresh, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [tick, refresh]);

  return { state, health, error, lastChecked, refresh };
}
