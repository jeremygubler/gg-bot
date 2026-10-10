import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { AuditEntry } from "./api";
import type { JsonObject } from "./schema";
import { LocaleProvider } from "./i18n";
import { Router } from "./router";
import { FakeBackend } from "./test/fakeBackend";

let backend: FakeBackend;

function mount(path = "/") {
  window.history.replaceState(null, "", path);
  return render(
    <LocaleProvider initial="de">
      <Router>
        <App />
      </Router>
    </LocaleProvider>,
  );
}

beforeEach(() => {
  backend = new FakeBackend();
  vi.stubGlobal("fetch", backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("Anmeldung", () => {
  it("ohne Sitzung: Login-Link auf die API, kein Fehler", async () => {
    backend.loggedIn = false;
    mount();

    const link = await screen.findByRole("link", { name: "Mit Discord anmelden" });

    expect(link).toHaveAttribute("href", "/api/auth/login");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("Login nicht eingerichtet: Hinweis statt Knopf", async () => {
    backend.disabled = true;
    mount();

    expect(await screen.findByRole("alert")).toHaveTextContent("nicht eingerichtet");
    expect(screen.queryByRole("link", { name: "Mit Discord anmelden" })).not.toBeInTheDocument();
  });

  it("Abmelden sendet den CSRF-Header und zeigt wieder den Login", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: "Abmelden" }));

    await screen.findByRole("link", { name: "Mit Discord anmelden" });
    const call = backend.calls.find((c) => c.path === "/api/auth/logout")!;
    expect(call.method).toBe("POST");
    expect(call.csrf).toBe("ggbot");
  });

  it("Sitzung läuft mitten in der Arbeit ab: zurück zum Login", async () => {
    const user = userEvent.setup();
    mount("/g/100/m/starboard");
    await screen.findByLabelText("Aktiv");

    backend.loggedIn = false;
    await user.click(screen.getByLabelText("Aktiv"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await screen.findByRole("link", { name: "Mit Discord anmelden" });
  });

  it("Netzwerkfehler beim Start: Fehlermeldung mit Wiederholen", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("offline");
    });
    mount();

    expect(await screen.findByRole("alert")).toHaveTextContent("Keine Verbindung");
    expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });
});

describe("Serverliste", () => {
  it("Server mit Bot öffnen, ohne Bot einladen (neuer Tab, mit Rechten und Server-ID)", async () => {
    mount();

    const open = await screen.findByRole("link", { name: "Öffnen" });
    expect(open).toHaveAttribute("href", "/g/100");

    const invite = screen.getByRole("link", { name: "Bot einladen" });
    const url = new URL(invite.getAttribute("href")!);
    expect(url.origin + url.pathname).toBe("https://discord.com/oauth2/authorize");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: "1234",
      scope: "bot applications.commands",
      permissions: "99",
      guild_id: "200",
      disable_guild_select: "true",
    });
    expect(invite).toHaveAttribute("target", "_blank");
    expect(invite.getAttribute("rel")).toContain("noopener");
  });

  it("leere Liste", async () => {
    backend.me.guilds = [];
    mount();
    expect(await screen.findByText(/verwaltest keinen Server/)).toBeInTheDocument();
  });

  it("Sprache umschalten", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("Deine Server");

    await user.click(screen.getByRole("button", { name: "Switch to English" }));

    expect(screen.getByText("Your servers")).toBeInTheDocument();
    expect(localStorage.getItem("ggbot.locale")).toBe("en");
    localStorage.removeItem("ggbot.locale");
  });
});

describe("Modul bearbeiten", () => {
  it("speichert den vollständigen Stand, meldet Erfolg und sperrt Speichern wieder", async () => {
    const user = userEvent.setup();
    mount("/g/100/m/starboard");
    const save = await screen.findByRole("button", { name: "Speichern" });
    expect(save).toBeDisabled();

    await user.click(screen.getByLabelText("Aktiv"));
    await user.selectOptions(screen.getByLabelText("Kanal"), "2");
    expect(screen.getByText("Ungespeicherte Änderungen")).toBeInTheDocument();
    await user.click(save);

    await screen.findByText("Gespeichert.");
    const put = backend.calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/api/guilds/100/settings/starboard");
    expect(put.csrf).toBe("ggbot");
    expect(put.body).toMatchObject({ enabled: true, channel_id: "2", threshold: 3 });
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    expect(screen.queryByText("Ungespeicherte Änderungen")).not.toBeInTheDocument();
  });

  it("zeigt 422 am Feld, behält die Eingabe und lässt erneutes Speichern zu", async () => {
    const user = userEvent.setup();
    backend.nextPut = {
      status: 422,
      body: { detail: { error: "invalid_settings", fields: [{ loc: ["threshold"], msg: "Input should be >= 1" }] } },
    };
    mount("/g/100/m/starboard");
    const threshold = await screen.findByLabelText("Schwelle");
    await user.clear(threshold);
    await user.type(threshold, "9");

    await user.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Input should be >= 1")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Bitte korrigiere die markierten Felder.");
    expect(screen.getByLabelText("Schwelle")).toHaveValue(9);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");
    expect(screen.queryByText("Input should be >= 1")).not.toBeInTheDocument();
  });

  it("Fehler ohne Feldort (Modell-Regel) erscheinen oben", async () => {
    const user = userEvent.setup();
    backend.nextPut = {
      status: 422,
      body: { detail: { error: "invalid_settings", fields: [{ loc: [], msg: "Value error, xp_min darf nicht größer sein" }] } },
    };
    mount("/g/100/m/starboard");
    await user.click(await screen.findByLabelText("Aktiv"));

    await user.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("xp_min darf nicht größer sein")).toBeInTheDocument();
  });

  it("Verwerfen setzt auf den gespeicherten Stand zurück", async () => {
    const user = userEvent.setup();
    mount("/g/100/m/starboard");
    const active = await screen.findByLabelText("Aktiv");

    await user.click(active);
    await user.click(screen.getByRole("button", { name: "Verwerfen" }));

    expect(screen.getByLabelText("Aktiv")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("andere Fehler (Limit) zeigen die übersetzte Meldung", async () => {
    const user = userEvent.setup();
    backend.nextPut = { status: 429, body: { detail: { error: "rate_limited" } } };
    mount("/g/100/m/starboard");
    await user.click(await screen.findByLabelText("Aktiv"));

    await user.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText(/Zu viele Änderungen/)).toBeInTheDocument();
  });

  it("Modulwechsel mit ungespeicherten Änderungen fragt nach", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    mount("/g/100/m/starboard");
    await user.click(await screen.findByLabelText("Aktiv"));

    await user.click(within(screen.getByRole("navigation")).getByText("Tickets"));

    expect(confirm).toHaveBeenCalled();
    expect(window.location.pathname).toBe("/g/100/m/starboard");

    confirm.mockReturnValue(true);
    await user.click(within(screen.getByRole("navigation")).getByText("Tickets"));
    await waitFor(() => expect(window.location.pathname).toBe("/g/100/m/tickets"));
    await screen.findByLabelText("Log-Kanal");
  });

  it("die Seitenleiste führt zu den Panels und markiert die aktuelle Seite", async () => {
    const user = userEvent.setup();
    mount("/g/100/m/starboard");
    await screen.findByLabelText("Aktiv");
    const nav = within(screen.getByRole("navigation"));

    await user.click(nav.getByText("Ticket-Panels"));

    expect(await screen.findByRole("heading", { name: "Ticket-Panels" })).toBeInTheDocument();
    expect(nav.getByText("Ticket-Panels")).toHaveAttribute("aria-current", "page");
    expect(nav.getByText("Rollen-Panels")).not.toHaveAttribute("aria-current");
    expect(window.location.pathname).toBe("/g/100/panels/tickets");
  });

  it("Server ohne Zugriff: Meldung mit Weg zurück", async () => {
    mount("/g/999/m/starboard");

    expect(await screen.findByRole("alert")).toHaveTextContent("nicht verfügbar");
    expect(screen.getByRole("link", { name: "Server" })).toBeInTheDocument();
  });

  it("unbekanntes Modul: Fehlermeldung statt leerer Seite", async () => {
    mount("/g/100/m/gibtsnicht");
    vi.spyOn(console, "error").mockImplementation(() => {});

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("unbekannter Pfad", async () => {
    mount("/irgendwas");
    expect(await screen.findByRole("alert")).toHaveTextContent("gibt es nicht");
  });
});

describe("Änderungsprotokoll", () => {
  const entry = (id: number, after: JsonObject, before: JsonObject | null): AuditEntry => ({
    id,
    user_id: "7",
    module: "starboard",
    before,
    after,
    at: "2026-01-02T03:04:05+00:00",
  });

  it("zeigt geänderte Felder mit Vorher und Nachher", async () => {
    const user = userEvent.setup();
    backend.audit = [entry(2, { threshold: 5, emoji: "⭐" }, { threshold: 3, emoji: "⭐" }), entry(1, { threshold: 3 }, null)];
    mount("/g/100/audit");

    await screen.findByText("1 Felder geändert");
    expect(screen.getByText("Erstmals gespeichert")).toBeInTheDocument();
    await user.click(screen.getByText("1 Felder geändert"));
    const first = screen.getAllByRole("listitem").find((li) => li.textContent?.includes("1 Felder geändert"))!;
    expect(within(first).getByText("Schwelle")).toBeInTheDocument();
    expect(within(first).queryByText("Emoji")).not.toBeInTheDocument();
  });

  it("leeres Protokoll", async () => {
    mount("/g/100/audit");
    expect(await screen.findByText(/Noch keine Änderungen/)).toBeInTheDocument();
  });

  it("lädt ältere Einträge nach, bis nichts mehr kommt", async () => {
    const user = userEvent.setup();
    backend.audit = Array.from({ length: 30 }, (_, i) => entry(30 - i, { threshold: i + 1 }, null));
    mount("/g/100/audit");

    await screen.findAllByText("Erstmals gespeichert");
    expect(screen.getAllByText("Erstmals gespeichert")).toHaveLength(25);

    await user.click(screen.getByRole("button", { name: "Ältere laden" }));

    await waitFor(() => expect(screen.getAllByText("Erstmals gespeichert")).toHaveLength(30));
    expect(screen.queryByRole("button", { name: "Ältere laden" })).not.toBeInTheDocument();
    const paging = backend.calls.filter((c) => c.path.includes("/audit"));
    expect(paging[1]!.path).toContain("before=6");
  });
});
