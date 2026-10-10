import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./api";

function fake(status: number, body?: unknown): typeof fetch {
  return vi.fn(async () =>
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  ) as unknown as typeof fetch;
}

describe("createApi", () => {
  it("schreibende Aufrufe senden den CSRF-Header, lesende nicht", async () => {
    const fetcher = fake(200, {});
    const api = createApi(fetcher);

    await api.me();
    await api.save("1", "starboard", { threshold: 3 });

    const calls = vi.mocked(fetcher).mock.calls;
    expect((calls[0]![1]!.headers as Record<string, string>)["X-Requested-With"]).toBeUndefined();
    const put = calls[1]![1]!;
    expect((put.headers as Record<string, string>)["X-Requested-With"]).toBe("ggbot");
    expect(put.method).toBe("PUT");
    expect(put.credentials).toBe("same-origin");
    expect(calls[1]![0]).toBe("/api/guilds/1/settings/starboard");
  });

  it("Abmelden sendet den CSRF-Header und akzeptiert 204", async () => {
    const fetcher = fake(204);
    await createApi(fetcher).logout();
    expect(vi.mocked(fetcher).mock.calls[0]![1]!.method).toBe("POST");
  });

  it("macht aus Fehlerantworten einen ApiError mit Schlüssel und Feldern", async () => {
    const fields = [{ loc: ["threshold"], msg: "zu klein" }];
    const api = createApi(fake(422, { detail: { error: "invalid_settings", fields } }));

    const error = await api.save("1", "starboard", {}).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "invalid_settings", fields });
  });

  it("kommt mit Nicht-JSON-Fehlern (Proxy-Seite) zurecht", async () => {
    const fetcher = vi.fn(async () => new Response("<html>502</html>", { status: 502 })) as unknown as typeof fetch;

    await expect(createApi(fetcher).me()).rejects.toMatchObject({ status: 502, code: "unknown" });
  });

  it("meldet Netzwerkfehler als code=network", async () => {
    const fetcher = vi.fn(async () => {
      throw new TypeError("offline");
    }) as unknown as typeof fetch;

    await expect(createApi(fetcher).me()).rejects.toMatchObject({ status: 0, code: "network" });
  });

  it("hängt die Blätterposition des Protokolls an", async () => {
    const fetcher = fake(200, []);
    await createApi(fetcher).audit("5", 40);
    expect(vi.mocked(fetcher).mock.calls[0]![0]).toBe("/api/guilds/5/audit?limit=25&before=40");
  });

  it("meldet 401 an die Oberfläche, andere Fehler nicht", async () => {
    const lost = vi.fn();

    await createApi(fake(401, { detail: { error: "unauthenticated" } }), lost).modules().catch(() => undefined);
    expect(lost).toHaveBeenCalledTimes(1);

    await createApi(fake(404, { detail: { error: "guild_not_found" } }), lost).snapshot("1").catch(() => undefined);
    await createApi(fake(500), lost).modules().catch(() => undefined);
    expect(lost).toHaveBeenCalledTimes(1);
  });
});
