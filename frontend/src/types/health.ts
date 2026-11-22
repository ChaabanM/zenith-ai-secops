export interface HealthResponse {
  status: "ok" | "degraded";
  app: string;
  version: string;
  environment: string;
  database: "connected" | "unavailable";
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown };
}
