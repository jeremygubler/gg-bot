import type { FieldError, Json, JsonObject } from "./schema";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly fields: FieldError[] = [],
  ) {
    super(`${status} ${code}`);
  }
}

export type MeGuild = { id: string; name: string; icon: string | null; owner: boolean; bot_present: boolean };
export type Me = {
  client_id: string;
  invite_permissions: string;
  id: string; username: string; avatar: string | null; guilds: MeGuild[] };
export type ModuleInfo = { module: string; title: string };
export type ChannelInfo = {
  id: string;
  name: string;
  type: string;
  position: number;
  parent_id: string | null;
};
export type RoleInfo = { id: string; name: string; color: number; position: number; managed: boolean };
export type Snapshot = {
  guild_id: string;
  name: string;
  channels: ChannelInfo[];
  roles: RoleInfo[];
  emojis: { id: string; name: string; animated: boolean }[];
};
export type AuditEntry = {
  id: number;
  user_id: string;
  module: string;
  before: JsonObject | null;
  after: JsonObject;
  at: string;
};

const SAFE = new Set(["GET", "HEAD"]);

/**
 * Dünner Wrapper um fetch. Schreibende Aufrufe tragen den Header, den die API gegen CSRF
 * verlangt. Fehler kommen als `ApiError` mit dem Schlüssel der API an, nie als Rohtext.
 */
export function createApi(
  fetcher: typeof fetch = (...args) => fetch(...args),
  /** Wird bei jeder 401-Antwort gerufen (Sitzung abgelaufen), damit die Oberfläche zum Login wechselt. */
  onUnauthenticated: () => void = () => {},
) {
  async function request<T>(method: string, path: string, body?: Json): Promise<T> {
    const headers: Record<string, string> = {};
    if (!SAFE.has(method)) headers["X-Requested-With"] = "ggbot";
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response: Response;
    try {
      response = await fetcher(`/api${path}`, {
        method,
        headers,
        credentials: "same-origin",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, "network");
    }
    if (response.ok) {
      return (response.status === 204 ? undefined : await response.json()) as T;
    }
    let code = "unknown";
    let fields: FieldError[] = [];
    try {
      const payload = (await response.json()) as { detail?: { error?: string; fields?: FieldError[] } };
      code = payload.detail?.error ?? code;
      fields = payload.detail?.fields ?? [];
    } catch {
      // Kein JSON (z. B. Fehlerseite des Proxys): der Statuscode genügt.
    }
    if (response.status === 401) onUnauthenticated();
    throw new ApiError(response.status, code, fields);
  }

  return {
    me: () => request<Me>("GET", "/me"),
    logout: () => request<void>("POST", "/auth/logout"),
    modules: () => request<ModuleInfo[]>("GET", "/modules"),
    schema: (module: string) => request<object>("GET", `/modules/${module}/schema`),
    snapshot: (guild: string) => request<Snapshot>("GET", `/guilds/${guild}/snapshot`),
    settings: (guild: string, module: string) =>
      request<JsonObject>("GET", `/guilds/${guild}/settings/${module}`),
    save: (guild: string, module: string, value: JsonObject) =>
      request<JsonObject>("PUT", `/guilds/${guild}/settings/${module}`, value),
    audit: (guild: string, before?: number) =>
      request<AuditEntry[]>("GET", `/guilds/${guild}/audit?limit=25${before ? `&before=${before}` : ""}`),
  };
}

export type Api = ReturnType<typeof createApi>;
