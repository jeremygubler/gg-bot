import type { FieldError, JsonObject } from "./schema";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly fields: FieldError[] = [],
    /** Bei `user_error`: der Text des Servers, schon in der Sprache der Oberfläche. */
    readonly userMessage: string | null = null,
  ) {
    super(`${status} ${code}`);
  }
}

export type MeGuild = {
  id: string;
  name: string;
  icon: string | null;
  owner: boolean;
  administrator: boolean;
  bot_present: boolean;
};
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
  bot_top_position: number;
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

export type RoleItem = {
  id: string;
  role_id: string;
  label: string | null;
  emoji: string | null;
  description: string | null;
  position: number;
};
export type RolePanel = {
  id: string;
  kind: "button" | "select" | "reaction";
  mode: "normal" | "unique" | "verify";
  title: string;
  description: string | null;
  channel_id: string | null;
  message_id: string | null;
  external: boolean;
  published: boolean;
  items: RoleItem[];
};
export type Category = {
  id: string;
  name: string;
  emoji: string | null;
  description: string | null;
  parent_id: string | null;
  support_role_ids: string[];
  welcome_message: string | null;
  questions: string[];
};
export type TicketPanel = {
  id: string;
  title: string;
  description: string | null;
  channel_id: string | null;
  message_id: string | null;
  published: boolean;
  categories: Category[];
};
export type PublishStatus = {
  state: "none" | "pending" | "done" | "error";
  message: string | null;
  notice: string | null;
};
export type RolePanelInput = { title: string; description: string | null; kind: RolePanel["kind"]; mode: RolePanel["mode"] };
export type RoleItemInput = { role_id: string; label: string | null; emoji: string | null; description: string | null };
export type CategoryInput = {
  name: string;
  support_role_id: string;
  emoji: string | null;
  description: string | null;
  parent_id: string | null;
  welcome_message: string | null;
  questions: string[];
};
export type EmbedField = { name: string; value: string; inline: boolean };
export type Embed = {
  title: string | null;
  description: string | null;
  color: number | null;
  author: string | null;
  footer: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
  fields: EmbedField[];
};
export type AnnouncementState = "draft" | "scheduled" | "sent" | "failed";
export type Announcement = {
  id: string;
  name: string;
  content: string | null;
  embed: Embed | null;
  state: AnnouncementState;
  channel_id: string | null;
  message_id: string | null;
  scheduled_at: string | null;
  sent_at: string | null;
  error: string | null;
};
export type AnnouncementInput = { name: string; content: string | null; embed: Embed | null };
export type CardKind = "rank" | "welcome";
export type CardStyle = { accent: number; background: number; template: string };
export type PublicRow = { position: number; name: string | null; avatar_url: string | null; level: number; xp: number };
export type PublicLeaderboard = { guild_name: string; page: number; pages: number; rows: PublicRow[] };

const SAFE = new Set(["GET", "HEAD"]);

/**
 * Dünner Wrapper um fetch. Schreibende Aufrufe tragen den Header, den die API gegen CSRF
 * verlangt. Fehler kommen als `ApiError` mit dem Schlüssel der API an, nie als Rohtext.
 */
export function createApi(
  fetcher: typeof fetch = (...args) => fetch(...args),
  /** Wird bei jeder 401-Antwort gerufen (Sitzung abgelaufen), damit die Oberfläche zum Login wechselt. */
  onUnauthenticated: () => void = () => {},
  /** Sprache der Oberfläche: der Server übersetzt Fehlermeldungen damit (Kopf `X-Locale`). */
  locale: () => string = () => "de",
) {
  async function send(method: string, path: string, body?: unknown): Promise<Response> {
    const headers: Record<string, string> = { "X-Locale": locale() };
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
    if (response.ok) return response;
    let code = "unknown";
    let fields: FieldError[] = [];
    let userMessage: string | null = null;
    try {
      const payload = (await response.json()) as {
        detail?: { error?: string; fields?: FieldError[]; message?: string };
      };
      code = payload.detail?.error ?? code;
      fields = payload.detail?.fields ?? [];
      userMessage = payload.detail?.message ?? null;
    } catch {
      // Kein JSON (z. B. Fehlerseite des Proxys): der Statuscode genügt.
    }
    if (response.status === 401) onUnauthenticated();
    throw new ApiError(response.status, code, fields, userMessage);
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await send(method, path, body);
    return (response.status === 204 ? undefined : await response.json()) as T;
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
    rolePanels: (guild: string) => request<RolePanel[]>("GET", `/guilds/${guild}/panels/roles`),
    createRolePanel: (guild: string, input: RolePanelInput) =>
      request<RolePanel>("POST", `/guilds/${guild}/panels/roles`, input),
    updateRolePanel: (guild: string, id: string, input: Pick<RolePanelInput, "title" | "description" | "mode">) =>
      request<RolePanel>("PUT", `/guilds/${guild}/panels/roles/${id}`, input),
    deleteRolePanel: (guild: string, id: string) =>
      request<void>("DELETE", `/guilds/${guild}/panels/roles/${id}`),
    addRoleItem: (guild: string, id: string, input: RoleItemInput) =>
      request<RolePanel>("POST", `/guilds/${guild}/panels/roles/${id}/items`, input),
    removeRoleItem: (guild: string, id: string, roleId: string) =>
      request<RolePanel>("DELETE", `/guilds/${guild}/panels/roles/${id}/items/${roleId}`),
    publishRolePanel: (guild: string, id: string, channel: string) =>
      request<PublishStatus>("POST", `/guilds/${guild}/panels/roles/${id}/publish`, { channel_id: channel }),
    rolePanelStatus: (guild: string, id: string) =>
      request<PublishStatus>("GET", `/guilds/${guild}/panels/roles/${id}/status`),
    ticketPanels: (guild: string) => request<TicketPanel[]>("GET", `/guilds/${guild}/panels/tickets`),
    createTicketPanel: (guild: string, input: { title: string; description: string | null }) =>
      request<TicketPanel>("POST", `/guilds/${guild}/panels/tickets`, input),
    updateTicketPanel: (guild: string, id: string, input: { title: string; description: string | null }) =>
      request<TicketPanel>("PUT", `/guilds/${guild}/panels/tickets/${id}`, input),
    deleteTicketPanel: (guild: string, id: string) =>
      request<void>("DELETE", `/guilds/${guild}/panels/tickets/${id}`),
    addCategory: (guild: string, id: string, input: CategoryInput) =>
      request<TicketPanel>("POST", `/guilds/${guild}/panels/tickets/${id}/categories`, input),
    removeCategory: (guild: string, id: string, categoryId: string) =>
      request<TicketPanel>("DELETE", `/guilds/${guild}/panels/tickets/${id}/categories/${categoryId}`),
    addSupportRole: (guild: string, id: string, categoryId: string, roleId: string) =>
      request<TicketPanel>("POST", `/guilds/${guild}/panels/tickets/${id}/categories/${categoryId}/roles`, {
        role_id: roleId,
      }),
    removeSupportRole: (guild: string, id: string, categoryId: string, roleId: string) =>
      request<TicketPanel>(
        "DELETE",
        `/guilds/${guild}/panels/tickets/${id}/categories/${categoryId}/roles/${roleId}`,
      ),
    publishTicketPanel: (guild: string, id: string, channel: string) =>
      request<PublishStatus>("POST", `/guilds/${guild}/panels/tickets/${id}/publish`, { channel_id: channel }),
    ticketPanelStatus: (guild: string, id: string) =>
      request<PublishStatus>("GET", `/guilds/${guild}/panels/tickets/${id}/status`),
    announcements: (guild: string) => request<Announcement[]>("GET", `/guilds/${guild}/announcements`),
    announcement: (guild: string, id: string) => request<Announcement>("GET", `/guilds/${guild}/announcements/${id}`),
    createAnnouncement: (guild: string, input: AnnouncementInput) =>
      request<Announcement>("POST", `/guilds/${guild}/announcements`, input),
    updateAnnouncement: (guild: string, id: string, input: AnnouncementInput) =>
      request<Announcement>("PUT", `/guilds/${guild}/announcements/${id}`, input),
    deleteAnnouncement: (guild: string, id: string) =>
      request<void>("DELETE", `/guilds/${guild}/announcements/${id}`),
    sendAnnouncement: (guild: string, id: string, channel: string, scheduledAt: string | null) =>
      request<Announcement>("POST", `/guilds/${guild}/announcements/${id}/send`, {
        channel_id: channel,
        scheduled_at: scheduledAt,
      }),
    cancelAnnouncement: (guild: string, id: string) =>
      request<Announcement>("POST", `/guilds/${guild}/announcements/${id}/cancel`),
    preview: async (guild: string, card: CardKind, style: CardStyle): Promise<Blob> =>
      (await send("POST", `/guilds/${guild}/preview/${card}`, style)).blob(),
    publicLeaderboard: (guild: string, page: number) =>
      request<PublicLeaderboard>("GET", `/public/${guild}/leaderboard?page=${page}`),
    audit: (guild: string, before?: number) =>
      request<AuditEntry[]>("GET", `/guilds/${guild}/audit?limit=25${before ? `&before=${before}` : ""}`),
  };
}

export type Api = ReturnType<typeof createApi>;
