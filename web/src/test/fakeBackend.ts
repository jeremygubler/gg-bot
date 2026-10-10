import type { AuditEntry, Me } from "../api";
import type { JsonObject } from "../schema";
import { snapshot } from "./harness";
import { fixtures } from "./schemas";

export type Call = { method: string; path: string; body: unknown; csrf: string | null };

/** Spielt die API mit echten Schemas; protokolliert alle Aufrufe. Schlägt bei Unbekanntem laut fehl. */
export class FakeBackend {
  calls: Call[] = [];
  loggedIn = true;
  disabled = false;
  settings: Record<string, JsonObject> = {};
  audit: AuditEntry[] = [];
  /** Nächste Antwort auf einen PUT überschreiben (z. B. 422). */
  nextPut: { status: number; body: unknown } | null = null;
  me: Me = {
    client_id: "1234",
    invite_permissions: "99",
    id: "7",
    username: "Jeremy",
    avatar: null,
    guilds: [
      { id: "100", name: "Alpha", icon: null, owner: true, bot_present: true },
      { id: "200", name: "Beta", icon: "abc", owner: false, bot_present: false },
    ],
  };

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const body = init?.body === undefined ? undefined : JSON.parse(String(init.body));
    this.calls.push({ method, path: url.pathname + url.search, body, csrf: headers["X-Requested-With"] ?? null });
    const json = (status: number, payload: unknown) =>
      new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
    const error = (status: number, code: string) => json(status, { detail: { error: code } });

    if (this.disabled) return error(503, "dashboard_disabled");
    if (!this.loggedIn) return error(401, "unauthenticated");

    const path = url.pathname.replace(/^\/api/, "");
    if (path === "/me") return json(200, this.me);
    if (path === "/auth/logout") {
      this.loggedIn = false;
      return new Response(null, { status: 204 });
    }
    if (path === "/modules") {
      return json(200, Object.keys(fixtures).map((module) => ({ module, title: module })));
    }
    let match = /^\/modules\/(\w+)\/schema$/.exec(path);
    if (match) return json(200, fixtures[match[1]!]!.schema);

    match = /^\/guilds\/(\d+)\/(snapshot|audit|settings\/(\w+))$/.exec(path);
    if (match === null) throw new Error(`FakeBackend: unbekannter Pfad ${method} ${path}`);
    const [, guild, what, module] = match;
    if (guild !== "100") return error(404, "guild_not_found");
    if (what === "snapshot") return json(200, snapshot);
    if (what === "audit") {
      const before = url.searchParams.get("before");
      const rows = this.audit.filter((entry) => before === null || entry.id < Number(before));
      return json(200, rows.slice(0, Number(url.searchParams.get("limit") ?? 50)));
    }
    if (method === "PUT") {
      if (this.nextPut !== null) {
        const next = this.nextPut;
        this.nextPut = null;
        return json(next.status, next.body);
      }
      this.settings[module!] = body as JsonObject;
      return json(200, body);
    }
    return json(200, this.settings[module!] ?? fixtures[module!]!.defaults);
  };
}
