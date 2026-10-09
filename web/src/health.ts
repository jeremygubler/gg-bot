export type Health = { db: "ok" | "error"; redis: "ok" | "error" };

export type HealthResult =
  | { kind: "ok"; health: Health }
  | { kind: "degraded"; health: Health }
  | { kind: "unreachable" };

/** Fragt /api/healthz ab. 503 mit Body heisst: API läuft, ein Dienst dahinter nicht. */
export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<HealthResult> {
  try {
    const response = await fetcher("/api/healthz");
    const health = (await response.json()) as Health;
    return { kind: response.ok ? "ok" : "degraded", health };
  } catch {
    return { kind: "unreachable" };
  }
}
