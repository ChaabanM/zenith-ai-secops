import { vi } from "vitest";

export interface Call {
  method: string;
  url: URL;
  body: unknown;
}

type Handler = (call: Call) => { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;

/**
 * Routes fetch() by "METHOD /path" (query string ignored for matching) and records every call,
 * so tests can assert exactly what was sent to the backend.
 */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    const call = { method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const route = routes[`${method} ${url.pathname}`];
    if (route === undefined) return new Response(JSON.stringify({ error: { code: "not_found", message: `no mock for ${method} ${url.pathname}` } }), { status: 404 });
    const result = typeof route === "function" ? await (route as Handler)(call) : { body: route };
    return new Response(JSON.stringify(result.body), { status: result.status ?? 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock, last: (path: string) => [...calls].reverse().find((c) => c.url.pathname === path) };
}
