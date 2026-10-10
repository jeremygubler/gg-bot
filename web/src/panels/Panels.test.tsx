import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { RolePanel, TicketPanel } from "../api";
import { backendRef, mount, setupBackend } from "../test/mount";
import { grantableRoles } from "./RolePanels";
import { supportRoleChoices } from "./TicketPanels";
import { snapshot } from "../test/harness";

setupBackend();

function rolePanel(extra: Partial<RolePanel> = {}): RolePanel {
  return {
    id: "50",
    kind: "button",
    mode: "normal",
    title: "Spiele",
    description: null,
    channel_id: null,
    message_id: null,
    external: false,
    published: false,
    items: [],
    ...extra,
  };
}

function ticketPanel(extra: Partial<TicketPanel> = {}): TicketPanel {
  return {
    id: "60",
    title: "Support",
    description: null,
    channel_id: null,
    message_id: null,
    published: false,
    categories: [],
    ...extra,
  };
}

describe("Auswahl der Rollen", () => {
  it("bietet nur Rollen an, die der Bot vergeben darf, und keine doppelten", () => {
    const roles = grantableRoles(
      { ...snapshot, roles: [...snapshot.roles, { id: "12", name: "Verwaltet", color: 0, position: 2, managed: true }, { id: "13", name: "Zu hoch", color: 0, position: 10, managed: false }] },
      ["11"],
    );

    expect(roles.map((r) => r.label)).toEqual(["Mod"]);
  });

  it("Support-Rollen: alles ausser @everyone und schon gewählten", () => {
    expect(supportRoleChoices(snapshot.roles, snapshot.guild_id, ["10"]).map((r) => r.label)).toEqual(["Member"]);
  });
});

describe("Rollen-Panels", () => {
  it("legt ein Panel an und zeigt es", async () => {
    const user = userEvent.setup();
    mount("/g/100/panels/roles");
    await screen.findByText("Noch keine Panels.");

    await user.click(screen.getByText("Neues Panel"));
    await user.type(screen.getByLabelText("Titel"), "Spiele");
    await user.selectOptions(screen.getByLabelText("Art"), "Auswahlmenü");
    await user.click(screen.getByRole("button", { name: "Anlegen" }));

    expect(await screen.findByRole("region", { name: "Spiele" })).toBeInTheDocument();
    const call = backendRef.current.calls.find((c) => c.method === "POST" && c.path.endsWith("/panels/roles"))!;
    expect(call.body).toEqual({ title: "Spiele", description: null, kind: "select", mode: "normal" });
    expect(call.csrf).toBe("ggbot");
  });

  it("zeigt den Fehler des Servers in der Sprache der Oberfläche und behält die Eingabe", async () => {
    const user = userEvent.setup();
    mount("/g/100/panels/roles");
    await screen.findByText("Noch keine Panels.");
    await user.click(screen.getByText("Neues Panel"));
    await user.type(screen.getByLabelText("Titel"), " ");
    // Absenden trotz leerem Titel erzwingen: der Server entscheidet.
    const form = screen.getByRole("button", { name: "Anlegen" }).closest("form")!;
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Der Titel fehlt.");
    expect(backendRef.current.calls.at(-1)!.locale).toBe("de");
  });

  it("Eintrag hinzufügen: nur vergebbare Rollen, danach in der Liste; entfernen", async () => {
    const user = userEvent.setup();
    backendRef.current.rolePanels = [rolePanel()];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    await user.click(within(panel).getByText("Eintrag hinzufügen", { selector: "summary" }));
    const role = within(panel).getByLabelText("Rolle");
    expect(within(role).queryByRole("option", { name: "@everyone" })).not.toBeInTheDocument();
    await user.selectOptions(role, "Mod");
    await user.type(within(panel).getByLabelText("Beschriftung"), "Moderator");
    await user.type(within(panel).getByLabelText("Emoji"), "🛡");
    await user.click(within(panel).getByRole("button", { name: "Eintrag hinzufügen" }));

    expect(await within(panel).findByText(/Moderator/)).toBeInTheDocument();
    expect(backendRef.current.calls.find((c) => c.path.endsWith("/items"))!.body).toEqual({
      role_id: "10",
      label: "Moderator",
      emoji: "🛡",
      description: null,
    });

    await user.click(within(panel).getByRole("button", { name: "Entfernen: Moderator" }));
    await waitFor(() => expect(within(panel).getByText("Noch keine Einträge.")).toBeInTheDocument());
  });

  it("Kurzbeschreibung gibt es nur beim Auswahlmenü", async () => {
    const user = userEvent.setup();
    backendRef.current.rolePanels = [rolePanel({ kind: "button" }), rolePanel({ id: "51", kind: "select", title: "Menü" })];
    mount("/g/100/panels/roles");

    const button = await screen.findByRole("region", { name: "Spiele" });
    const menu = screen.getByRole("region", { name: "Menü" });
    await user.click(within(button).getByText("Eintrag hinzufügen", { selector: "summary" }));
    await user.click(within(menu).getByText("Eintrag hinzufügen", { selector: "summary" }));

    expect(within(button).queryByLabelText(/Kurzbeschreibung/)).not.toBeInTheDocument();
    expect(within(menu).getByLabelText(/Kurzbeschreibung/)).toBeInTheDocument();
  });

  it("Löschen fragt nach und bricht bei „Abbrechen“ ab", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    backendRef.current.rolePanels = [rolePanel()];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    await user.click(within(panel).getByRole("button", { name: "Löschen" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Spiele"));
    expect(backendRef.current.rolePanels).toHaveLength(1);

    confirm.mockReturnValue(true);
    await user.click(within(panel).getByRole("button", { name: "Löschen" }));
    await screen.findByText("Noch keine Panels.");
    expect(backendRef.current.rolePanels).toHaveLength(0);
  });

  it("bearbeiten ändert Titel und Modus", async () => {
    const user = userEvent.setup();
    backendRef.current.rolePanels = [rolePanel()];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    await user.click(within(panel).getByRole("button", { name: "Bearbeiten" }));
    const title = within(panel).getByDisplayValue("Spiele");
    await user.clear(title);
    await user.type(title, "Hobbys");
    await user.selectOptions(within(panel).getByLabelText("Modus"), "Eine Rolle gleichzeitig");
    await user.click(within(panel).getByRole("button", { name: "Speichern" }));

    expect(await screen.findByRole("region", { name: "Hobbys" })).toBeInTheDocument();
    expect(backendRef.current.rolePanels[0]).toMatchObject({ title: "Hobbys", mode: "unique" });
  });

  it("ohne Administrator: ansehen ja, ändern nein, mit Hinweis", async () => {
    backendRef.current.me.guilds[0]!.administrator = false;
    backendRef.current.rolePanels = [rolePanel({ items: [{ id: "1", role_id: "10", label: "Mod", emoji: null, description: null, position: 0 }] })];
    mount("/g/100/panels/roles");

    const panel = await screen.findByRole("region", { name: "Spiele" });

    expect(screen.getByRole("note")).toHaveTextContent("nur der Eigentümer und Administratoren");
    expect(within(panel).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText("Neues Panel")).not.toBeInTheDocument();
    expect(panel).toHaveTextContent("→ Mod");
  });

  it("fremde Nachricht (extern): kein Veröffentlichen und kein Eintragsformular", async () => {
    backendRef.current.rolePanels = [rolePanel({ kind: "reaction", external: true, published: true, channel_id: "2", message_id: "5" })];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    expect(within(panel).getByText(/fremden Nachricht/)).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /veröffentlichen/i })).not.toBeInTheDocument();
    expect(within(panel).queryByText("Eintrag hinzufügen", { selector: "summary" })).not.toBeInTheDocument();
  });
});

describe("Veröffentlichen", () => {
  it("schickt den Auftrag, zeigt das Ergebnis des Bots und lädt neu", async () => {
    const user = userEvent.setup();
    backendRef.current.rolePanels = [rolePanel()];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });
    const publish = within(panel).getByRole("button", { name: "Veröffentlichen" });
    expect(publish).toBeDisabled(); // erst Kanal wählen

    const channel = within(panel).getByLabelText("Kanal");
    expect(within(channel).queryByRole("option", { name: /Lounge/ })).not.toBeInTheDocument(); // kein Sprachkanal
    await user.selectOptions(channel, "#chat (Allgemein)");
    await user.click(publish);

    expect(await within(panel).findByText("Veröffentlicht.")).toBeInTheDocument();
    expect(await within(panel).findByText("Veröffentlicht in #chat")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Neu veröffentlichen" })).toBeInTheDocument();
  });

  it("Fehler des Bots erscheinen im Wortlaut, ohne dass das Panel als veröffentlicht gilt", async () => {
    const user = userEvent.setup();
    backendRef.current.botAnswer = { state: "error", message: "Mir fehlen Rechte: Send Messages", notice: null };
    backendRef.current.rolePanels = [rolePanel()];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    await user.selectOptions(within(panel).getByLabelText("Kanal"), "#chat (Allgemein)");
    await user.click(within(panel).getByRole("button", { name: "Veröffentlichen" }));

    expect(await within(panel).findByText("Mir fehlen Rechte: Send Messages")).toBeInTheDocument();
    expect(within(panel).getByText("Nicht veröffentlicht")).toBeInTheDocument();
  });

  it("Hinweise des Bots (abgelehnte Reaktionen) erscheinen neben dem Erfolg", async () => {
    const user = userEvent.setup();
    backendRef.current.botAnswer = { state: "done", message: null, notice: "Diese Reaktionen konnte ich nicht setzen: 🎮" };
    backendRef.current.rolePanels = [rolePanel()];
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    await user.selectOptions(within(panel).getByLabelText("Kanal"), "#chat (Allgemein)");
    await user.click(within(panel).getByRole("button", { name: "Veröffentlichen" }));

    expect(await within(panel).findByText(/Diese Reaktionen konnte ich nicht setzen/)).toBeInTheDocument();
    expect(within(panel).getByText("Veröffentlicht.")).toBeInTheDocument();
  });

  it("Server-Fehler beim Auftrag (422) wird gezeigt", async () => {
    const user = userEvent.setup();
    backendRef.current.rolePanels = [rolePanel()];
    const original = backendRef.current.fetch;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/publish")
        ? new Response(JSON.stringify({ detail: { error: "user_error", message: "Dieser Kanal ist für Panels nicht verfügbar." } }), { status: 422 })
        : original(input, init),
    );
    mount("/g/100/panels/roles");
    const panel = await screen.findByRole("region", { name: "Spiele" });

    await user.selectOptions(within(panel).getByLabelText("Kanal"), "#chat (Allgemein)");
    await user.click(within(panel).getByRole("button", { name: "Veröffentlichen" }));

    expect(await within(panel).findByText("Dieser Kanal ist für Panels nicht verfügbar.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Veröffentlichen" })).toBeEnabled();
  });

  it("ohne Rückmeldung des Bots endet das Warten mit einem Hinweis", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      backendRef.current.botAnswer = { state: "none", message: null, notice: null };
      backendRef.current.rolePanels = [rolePanel()];
      mount("/g/100/panels/roles");
      const panel = await screen.findByRole("region", { name: "Spiele" });
      await user.selectOptions(within(panel).getByLabelText("Kanal"), "#chat (Allgemein)");
      await user.click(within(panel).getByRole("button", { name: "Veröffentlichen" }));
      expect(await within(panel).findByText("Wird veröffentlicht …")).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(31_000);

      expect(await within(panel).findByText(/Keine Rückmeldung vom Bot/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Ticket-Panels", () => {
  it("Panel anlegen und Kategorie mit allen Feldern hinzufügen", async () => {
    const user = userEvent.setup();
    backendRef.current.ticketPanels = [ticketPanel()];
    mount("/g/100/panels/tickets");
    const panel = await screen.findByRole("region", { name: "Support" });

    await user.click(within(panel).getByText("Kategorie hinzufügen", { selector: "summary" }));
    const form = within(panel).getByRole("form", { name: "Kategorie hinzufügen" });
    await user.type(within(form).getByLabelText("Name"), "Hilfe");
    await user.type(within(form).getByLabelText("Emoji"), "🛟");
    await user.selectOptions(within(form).getByLabelText("Support-Rolle"), "Mod");
    await user.selectOptions(within(form).getByLabelText(/Discord-Kategorie/), "Allgemein");
    await user.type(within(form).getByLabelText("Fragen (eine pro Zeile, höchstens 5)"), "Was ist los?{Enter}{Enter}Seit wann?");
    await user.click(within(form).getByRole("button", { name: "Kategorie hinzufügen" }));

    expect(await within(panel).findByRole("listitem", { name: "Hilfe" })).toBeInTheDocument();
    expect(backendRef.current.calls.find((c) => c.path.endsWith("/categories"))!.body).toEqual({
      name: "Hilfe",
      support_role_id: "10",
      emoji: "🛟",
      description: null,
      parent_id: "1",
      welcome_message: null,
      questions: ["Was ist los?", "Seit wann?"],
    });
  });

  it("Serverfehler bei der Kategorie bleiben am Formular", async () => {
    const user = userEvent.setup();
    backendRef.current.ticketPanels = [ticketPanel()];
    mount("/g/100/panels/tickets");
    const panel = await screen.findByRole("region", { name: "Support" });
    await user.click(within(panel).getByText("Kategorie hinzufügen", { selector: "summary" }));
    const form = within(panel).getByRole("form", { name: "Kategorie hinzufügen" });
    await user.type(within(form).getByLabelText("Name"), "Hilfe");
    await user.selectOptions(within(form).getByLabelText("Support-Rolle"), "Mod");
    // Der Fake lehnt die Rolle mit der ID des Servers ab; über das Backend simulieren.
    backendRef.current.ticketPanels[0]!.categories = [];
    const original = backendRef.current.fetch;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/categories")
        ? new Response(JSON.stringify({ detail: { error: "user_error", message: "Die Kategorie gibt es schon." } }), { status: 422 })
        : original(input, init),
    );

    await user.click(within(form).getByRole("button", { name: "Kategorie hinzufügen" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("Die Kategorie gibt es schon.");
    expect(within(form).getByLabelText("Name")).toHaveValue("Hilfe");
  });

  it("Support-Rollen hinzufügen und entfernen, Kategorie entfernen", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendRef.current.ticketPanels = [
      ticketPanel({
        categories: [{ id: "70", name: "Hilfe", emoji: null, description: null, parent_id: null, support_role_ids: ["10"], welcome_message: null, questions: [] }],
      }),
    ];
    mount("/g/100/panels/tickets");
    const category = await screen.findByRole("listitem", { name: "Hilfe" });

    const chips = within(category).getByRole("list", { name: "Support-Rollen" });
    await user.selectOptions(within(category).getByLabelText("Hilfe: Rolle hinzufügen"), "Member");
    await user.click(within(category).getByRole("button", { name: "Hinzufügen" }));
    expect(await within(chips).findByText("Member")).toBeInTheDocument();

    await user.click(within(category).getByRole("button", { name: "Entfernen: Member" }));
    await waitFor(() => expect(within(chips).queryByText("Member")).not.toBeInTheDocument());

    await user.click(within(category).getByRole("button", { name: "Löschen" }));
    await screen.findByText("Noch keine Kategorien.");
  });

  it("Ticket-Panels darf auch ein Nutzer ohne Administratorrecht ändern", async () => {
    backendRef.current.me.guilds[0]!.administrator = false;
    mount("/g/100/panels/tickets");

    expect(await screen.findByText("Neues Panel")).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("Veröffentlichen eines Ticket-Panels nutzt die Ticket-Route", async () => {
    const user = userEvent.setup();
    backendRef.current.ticketPanels = [ticketPanel()];
    mount("/g/100/panels/tickets");
    const panel = await screen.findByRole("region", { name: "Support" });

    await user.selectOptions(within(panel).getByLabelText("Kanal"), "#chat (Allgemein)");
    await user.click(within(panel).getByRole("button", { name: "Veröffentlichen" }));

    await within(panel).findByText("Veröffentlicht.");
    expect(backendRef.current.calls.some((c) => c.path === "/api/guilds/100/panels/tickets/60/publish")).toBe(true);
  });
});
