import type {
  Announcement,
  AuditEntry,
  Category,
  Me,
  PublicLeaderboard,
  PublishStatus,
  RoleItem,
  RolePanel,
  TicketPanel,
} from "../api";
import type { JsonObject } from "../schema";
import { snapshot } from "./harness";
import { fixtures } from "./schemas";

export type Call = { method: string; path: string; body: unknown; csrf: string | null; locale: string | null };

/** 1×1-PNG, genug für <img> und Blob. */
const PNG = Uint8Array.from(
  atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="),
  (c) => c.charCodeAt(0),
);

/** Spielt die API mit echten Schemas; protokolliert alle Aufrufe. Schlägt bei Unbekanntem laut fehl. */
export class FakeBackend {
  calls: Call[] = [];
  loggedIn = true;
  disabled = false;
  settings: Record<string, JsonObject> = {};
  audit: AuditEntry[] = [];
  /** Nächste Antwort auf einen PUT überschreiben (z. B. 422). */
  nextPut: { status: number; body: unknown } | null = null;
  rolePanels: RolePanel[] = [];
  ticketPanels: TicketPanel[] = [];
  announcements: Announcement[] = [];
  /** Was der Bot mit einer fälligen Ankündigung macht, sobald das Dashboard nachfragt. */
  botSends: "sent" | "failed" | null = null;
  /** Antwort des Bots auf eine Veröffentlichung (sofort vorhanden); `none` = keine Rückmeldung. */
  botAnswer: PublishStatus = { state: "done", message: null, notice: null };
  statuses = new Map<string, PublishStatus>();
  previews: unknown[] = [];
  previewFails = false;
  board: PublicLeaderboard | { error: string; status: number } = { guild_name: "Alpha", page: 1, pages: 1, rows: [] };
  private nextId = 1;
  me: Me = {
    client_id: "1234",
    invite_permissions: "99",
    id: "7",
    username: "Jeremy",
    avatar: null,
    guilds: [
      { id: "100", name: "Alpha", icon: null, owner: true, administrator: true, bot_present: true },
      { id: "200", name: "Beta", icon: "abc", owner: false, administrator: false, bot_present: false },
    ],
  };

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const body = init?.body === undefined ? undefined : JSON.parse(String(init.body));
    this.calls.push({
      method,
      path: url.pathname + url.search,
      body,
      csrf: headers["X-Requested-With"] ?? null,
      locale: headers["X-Locale"] ?? null,
    });
    const json = (status: number, payload: unknown) =>
      new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
    const error = (status: number, code: string) => json(status, { detail: { error: code } });
    const path = url.pathname.replace(/^\/api/, "");

    const publicMatch = /^\/public\/(\d+)\/leaderboard$/.exec(path);
    if (publicMatch) {
      if ("error" in this.board) return error(this.board.status, this.board.error);
      return json(200, { ...this.board, page: Number(url.searchParams.get("page") ?? 1) });
    }

    if (this.disabled) return error(503, "dashboard_disabled");
    if (!this.loggedIn) return error(401, "unauthenticated");

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

    match = /^\/guilds\/(\d+)\/(.*)$/.exec(path);
    if (match === null) throw new Error(`FakeBackend: unbekannter Pfad ${method} ${path}`);
    const [, guild, rest] = match as unknown as [string, string, string];
    if (guild !== "100") return error(404, "guild_not_found");
    if (rest === "snapshot") return json(200, snapshot);
    if (rest === "audit") {
      const before = url.searchParams.get("before");
      const rows = this.audit.filter((entry) => before === null || entry.id < Number(before));
      return json(200, rows.slice(0, Number(url.searchParams.get("limit") ?? 50)));
    }
    const preview = /^preview\/(rank|welcome)$/.exec(rest);
    if (preview) {
      this.previews.push(body);
      if (this.previewFails) return error(500, "unknown");
      return new Response(PNG, { status: 200, headers: { "Content-Type": "image/png" } });
    }
    if (rest.startsWith("announcements")) return this.announcementRoute(method, rest, body, json);
    if (rest.startsWith("panels/")) return this.panels(method, rest.slice("panels/".length), body, json, error);
    const settings = /^settings\/(\w+)$/.exec(rest);
    if (settings === null) throw new Error(`FakeBackend: unbekannter Pfad ${method} ${path}`);
    const module = settings[1]!;
    if (method === "PUT") {
      if (this.nextPut !== null) {
        const next = this.nextPut;
        this.nextPut = null;
        return json(next.status, next.body);
      }
      this.settings[module] = body as JsonObject;
      return json(200, body);
    }
    return json(200, this.settings[module] ?? fixtures[module]!.defaults);
  };

  private userError(json: (s: number, p: unknown) => Response, message: string): Response {
    return json(422, { detail: { error: "user_error", message } });
  }

  private announcementRoute(
    method: string,
    rest: string,
    body: any, // eslint-disable-line @typescript-eslint/no-explicit-any
    json: (s: number, p: unknown) => Response,
  ): Response {
    const parts = rest.split("/");
    if (parts.length === 1) {
      if (method === "GET") return json(200, this.announcements);
      if (!String(body.name).trim()) return this.userError(json, "Gib der Ankündigung einen Namen.");
      if (!body.content && !body.embed) return this.userError(json, "Die Nachricht ist leer.");
      const created: Announcement = {
        id: String(this.nextId++),
        name: body.name,
        content: body.content,
        embed: body.embed,
        state: "draft",
        channel_id: null,
        message_id: null,
        scheduled_at: null,
        sent_at: null,
        error: null,
      };
      this.announcements.unshift(created);
      return json(201, created);
    }
    const entry = this.announcements.find((a) => a.id === parts[1]);
    if (entry === undefined) return this.userError(json, "Diese Ankündigung gibt es nicht.");
    if (parts.length === 2) {
      if (method === "GET") {
        const due = entry.state === "scheduled" && Date.parse(entry.scheduled_at!) <= Date.now() + 10_000;
        if (due && this.botSends === "sent") Object.assign(entry, { state: "sent", message_id: "900", sent_at: entry.scheduled_at });
        if (due && this.botSends === "failed") Object.assign(entry, { state: "failed", error: "Der Bot darf dort nicht schreiben." });
        return json(200, entry);
      }
      if (method === "DELETE") {
        this.announcements = this.announcements.filter((a) => a !== entry);
        return json(200, null);
      }
      if (!body.content && !body.embed) return this.userError(json, "Die Nachricht ist leer.");
      Object.assign(entry, { name: body.name, content: body.content, embed: body.embed });
      return json(200, entry);
    }
    if (parts[2] === "send") {
      if (entry.state === "sent") return this.userError(json, "Schon gesendet.");
      Object.assign(entry, {
        state: "scheduled",
        channel_id: body.channel_id,
        scheduled_at: body.scheduled_at ?? new Date().toISOString(),
        error: null,
      });
      return json(202, entry);
    }
    if (parts[2] === "cancel") {
      if (entry.state !== "scheduled") return this.userError(json, "Nicht geplant.");
      Object.assign(entry, { state: "draft", scheduled_at: null });
      return json(200, entry);
    }
    throw new Error(`FakeBackend: unbekannter Ankündigungs-Pfad ${method} ${rest}`);
  }

  private panels(
    method: string,
    rest: string,
    body: any, // eslint-disable-line @typescript-eslint/no-explicit-any
    json: (s: number, p: unknown) => Response,
    error: (s: number, c: string) => Response,
  ): Response {
    const parts = rest.split("/");
    const kind = parts[0] as "roles" | "tickets";
    const panelId = parts[1];
    const key = `${kind}/${panelId}`;
    const list = kind === "roles" ? this.rolePanels : this.ticketPanels;
    const find = () => (list as { id: string }[]).find((p) => p.id === panelId);

    if (parts.length === 1) {
      if (method === "GET") return json(200, list);
      if (kind === "roles" && !this.me.guilds[0]!.administrator) return error(403, "needs_administrator");
      if (!String(body.title).trim()) return this.userError(json, "Der Titel fehlt.");
      const id = String(this.nextId++);
      if (kind === "roles") {
        const panel: RolePanel = {
          id,
          kind: body.kind,
          mode: body.mode,
          title: body.title,
          description: body.description,
          channel_id: null,
          message_id: null,
          external: false,
          published: false,
          items: [],
        };
        this.rolePanels.push(panel);
        return json(201, panel);
      }
      const panel: TicketPanel = {
        id,
        title: body.title,
        description: body.description,
        channel_id: null,
        message_id: null,
        published: false,
        categories: [],
      };
      this.ticketPanels.push(panel);
      return json(201, panel);
    }

    const panel = find();
    if (panel === undefined) return this.userError(json, "Panel nicht gefunden.");
    const section = parts[2];
    if (section === undefined) {
      if (method === "DELETE") {
        list.splice(list.indexOf(panel as never), 1);
        return new Response(null, { status: 204 });
      }
      Object.assign(panel, { title: body.title, description: body.description });
      if (kind === "roles") (panel as RolePanel).mode = body.mode;
      return json(200, panel);
    }
    if (section === "status") return json(200, this.statuses.get(key) ?? { state: "none", message: null, notice: null });
    if (section === "publish") {
      this.statuses.set(key, this.botAnswer);
      if (this.botAnswer.state === "done") {
        Object.assign(panel, { channel_id: body.channel_id, message_id: "900", published: true });
      }
      if (this.botAnswer.state === "none") this.statuses.delete(key);
      return json(202, { state: "pending", message: null, notice: null });
    }
    if (kind === "roles" && section === "items") {
      const rp = panel as RolePanel;
      if (method === "POST") {
        if (rp.items.some((i) => i.role_id === body.role_id)) return this.userError(json, "Rolle schon im Panel.");
        const item: RoleItem = { id: String(this.nextId++), position: rp.items.length, ...body };
        rp.items.push(item);
        return json(201, rp);
      }
      rp.items = rp.items.filter((i) => i.role_id !== parts[3]);
      return json(200, rp);
    }
    if (kind === "tickets" && section === "categories") {
      const tp = panel as TicketPanel;
      if (parts.length === 3) {
        if (body.support_role_id === "100") return this.userError(json, "@everyone geht nicht.");
        const category: Category = {
          id: String(this.nextId++),
          name: body.name,
          emoji: body.emoji,
          description: body.description,
          parent_id: body.parent_id,
          support_role_ids: [body.support_role_id],
          welcome_message: body.welcome_message,
          questions: body.questions,
        };
        tp.categories.push(category);
        return json(201, tp);
      }
      const category = tp.categories.find((c) => c.id === parts[3]);
      if (category === undefined) return this.userError(json, "Kategorie weg.");
      if (parts.length === 4) {
        tp.categories = tp.categories.filter((c) => c !== category);
        return json(200, tp);
      }
      if (method === "POST") category.support_role_ids.push(body.role_id);
      else category.support_role_ids = category.support_role_ids.filter((r) => r !== parts[5]);
      return json(200, tp);
    }
    throw new Error(`FakeBackend: unbekannter Panel-Pfad ${method} ${rest}`);
  }
}
