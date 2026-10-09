import { describe, expect, it } from "vitest";
import { fetchHealth } from "./health";

const reply = (status: number, body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;

describe("fetchHealth", () => {
  it("meldet ok bei 200", async () => {
    const result = await fetchHealth(reply(200, { db: "ok", redis: "ok" }));
    expect(result.kind).toBe("ok");
  });

  it("meldet degraded bei 503 mit Body", async () => {
    const result = await fetchHealth(reply(503, { db: "ok", redis: "error" }));
    expect(result).toEqual({ kind: "degraded", health: { db: "ok", redis: "error" } });
  });

  it("meldet unreachable bei Netzwerkfehler", async () => {
    const failing = (async () => {
      throw new TypeError("network");
    }) as typeof fetch;
    expect((await fetchHealth(failing)).kind).toBe("unreachable");
  });

  it("meldet unreachable bei Nicht-JSON (z. B. Proxy-Fehlerseite)", async () => {
    const html = (async () => new Response("<html>502</html>", { status: 502 })) as typeof fetch;
    expect((await fetchHealth(html)).kind).toBe("unreachable");
  });
});
