import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Announcement } from "../api";
import { backendRef, mount, setupBackend } from "../test/mount";
import { colorToHex, hexToColor } from "./EmbedPreview";
import { localToIso, toDraft, toEmbed } from "./Announcements";

setupBackend();

function entry(extra: Partial<Announcement> = {}): Announcement {
  return {
    id: "70",
    name: "Eröffnung",
    content: "Hallo zusammen",
    embed: {
      title: "Willkommen",
      description: "Schön, dass ihr da seid",
      color: 0xff0000,
      author: null,
      footer: "Euer Team",
      image_url: null,
      thumbnail_url: null,
      fields: [{ name: "Wann", value: "Heute", inline: true }],
    },
    state: "draft",
    channel_id: null,
    message_id: null,
    scheduled_at: null,
    sent_at: null,
    error: null,
    ...extra,
  };
}

const PAGE = "/g/100/announcements";

describe("Hilfsfunktionen", () => {
  it("rechnet Farben zwischen Zahl und Hex um", () => {
    expect(colorToHex(0xff0000)).toBe("#ff0000");
    expect(colorToHex(255)).toBe("#0000ff");
    expect(colorToHex(null)).toBe("#000000");
    expect(hexToColor("#00ff7f")).toBe(0x00ff7f);
  });

  it("baut aus leeren Eingaben kein Embed, auch nicht aus einer Farbe allein", () => {
    expect(toEmbed({ ...toDraft(null), color: 255 })).toBeNull();
    expect(toEmbed({ ...toDraft(null), title: "  " })).toBeNull();
    expect(toEmbed({ ...toDraft(null), title: "T" })?.title).toBe("T");
    expect(toEmbed({ ...toDraft(null), fields: [{ name: "a", value: "b", inline: false }] })).not.toBeNull();
  });

  it("macht aus einem Eintrag und zurück denselben Entwurf", () => {
    const draft = toDraft(entry());
    expect(toEmbed(draft)).toEqual(entry().embed);
    expect(toDraft(null).fields).toEqual([]);
  });

  it("deutet die Zeit des Eingabefelds in der Zeitzone des Browsers", () => {
    expect(localToIso("2026-10-13T18:00")).toBe(new Date(2026, 9, 13, 18, 0).toISOString());
  });
});

describe("Ankündigungen", () => {
  it("zeigt die leere Liste und den Hinweis", async () => {
    mount(PAGE);
    expect(await screen.findByText("Noch keine Ankündigungen.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ankündigungen" })).toHaveAttribute("aria-current", "page");
  });

  it("legt eine Ankündigung an und schickt Text und Embed an die API", async () => {
    const user = userEvent.setup();
    mount(PAGE);
    await screen.findByText("Noch keine Ankündigungen.");

    await user.click(screen.getByRole("button", { name: "Neue Ankündigung" }));
    await user.type(screen.getByLabelText("Name (nur für diese Liste)"), "Start");
    await user.type(screen.getByLabelText("Nachrichtentext"), "Hallo");
    await user.type(screen.getByLabelText("Titel"), "Eröffnung");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await screen.findByText("Gespeichert.");
    const call = backendRef.current.calls.find((c) => c.method === "POST" && c.path.endsWith("/announcements"))!;
    expect(call.csrf).toBe("ggbot");
    expect(call.body).toEqual({
      name: "Start",
      content: "Hallo",
      embed: {
        title: "Eröffnung",
        description: null,
        color: null,
        author: null,
        footer: null,
        image_url: null,
        thumbnail_url: null,
        fields: [],
      },
    });
    // Die neue Ankündigung steht in der Liste, ohne dass der Editor neu aufgebaut wurde.
    const list = screen.getByRole("navigation", { name: "Ankündigungen" });
    expect(within(list).getByText("Start")).toBeInTheDocument();
    expect(within(list).getByText("Entwurf")).toBeInTheDocument();
  });

  it("sendet ohne Embed, wenn nichts eingetragen ist", async () => {
    const user = userEvent.setup();
    mount(PAGE);
    await screen.findByText("Noch keine Ankündigungen.");
    await user.click(screen.getByRole("button", { name: "Neue Ankündigung" }));
    await user.type(screen.getByLabelText("Name (nur für diese Liste)"), "Kurz");
    await user.type(screen.getByLabelText("Nachrichtentext"), "Nur Text");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await screen.findByText("Gespeichert.");
    const call = backendRef.current.calls.find((c) => c.method === "POST" && c.path.endsWith("/announcements"))!;
    expect((call.body as { embed: unknown }).embed).toBeNull();
  });

  it("zeigt die Vorschau live und lädt keine fremden Bilder", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));

    const preview = screen.getByRole("group", { name: "Vorschau" });
    expect(within(preview).getByText("Hallo zusammen")).toBeInTheDocument();
    expect(within(preview).getByText("Willkommen")).toBeInTheDocument();
    expect(within(preview).getByText("Euer Team")).toBeInTheDocument();
    expect(within(preview).getByTestId("embed")).toHaveStyle({ borderLeftColor: "#ff0000" });

    await user.type(screen.getByLabelText("Bild-Adresse"), "https://example.org/a.png");
    expect(within(preview).queryByRole("img")).toBeNull();
    expect(preview.querySelector("img")).toBeNull();
    await user.clear(screen.getByLabelText("Titel"));
    await user.type(screen.getByLabelText("Titel"), "Neu");
    expect(within(preview).getByText("Neu")).toBeInTheDocument();
  });

  it("fügt Felder hinzu und entfernt sie", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry({ embed: { ...entry().embed!, fields: [] } })];
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));

    await user.click(screen.getByRole("button", { name: "Feld hinzufügen" }));
    await user.type(screen.getByLabelText("Feldname"), "Ort");
    await user.type(screen.getByLabelText("Feldtext"), "Discord");
    expect(within(screen.getByRole("group", { name: "Vorschau" })).getByText("Ort")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Feld entfernen" }));
    expect(screen.queryByLabelText("Feldname")).toBeNull();
  });

  it("zeigt den Fehler des Servers und behält die Eingabe", async () => {
    const user = userEvent.setup();
    mount(PAGE);
    await screen.findByText("Noch keine Ankündigungen.");
    await user.click(screen.getByRole("button", { name: "Neue Ankündigung" }));
    await user.type(screen.getByLabelText("Name (nur für diese Liste)"), "Leer");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Die Nachricht ist leer.");
    expect(screen.getByLabelText("Name (nur für diese Liste)")).toHaveValue("Leer");
  });

  it("sendet sofort in den gewählten Kanal und zeigt das Ergebnis des Bots", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    backendRef.current.botSends = "sent";
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));

    const send = screen.getByRole("button", { name: "Jetzt senden" });
    expect(send).toBeDisabled(); // ohne Kanal
    await user.selectOptions(screen.getByLabelText("Kanal"), "2");
    await user.click(send);

    const call = backendRef.current.calls.find((c) => c.path.endsWith("/70/send"))!;
    expect(call.body).toEqual({ channel_id: "2", scheduled_at: null });
    expect(await screen.findByText("Gesendet in #chat", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText("Beim Speichern wird die gesendete Nachricht in Discord angepasst.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt senden" })).toBeNull();
  }, 10000);

  it("zeigt einen Fehler des Bots und erlaubt erneutes Senden", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    backendRef.current.botSends = "failed";
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));
    await user.selectOptions(screen.getByLabelText("Kanal"), "2");
    await user.click(screen.getByRole("button", { name: "Jetzt senden" }));

    expect(await screen.findByText("Der Bot darf dort nicht schreiben.", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Erneut senden" })).toBeEnabled();
  }, 10000);

  it("plant eine Ankündigung mit Zeit und sagt sie wieder ab", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));
    await user.selectOptions(screen.getByLabelText("Kanal"), "2");
    await user.click(screen.getByLabelText("Zu einer bestimmten Zeit"));

    expect(screen.getByRole("button", { name: "Planen" })).toBeDisabled(); // ohne Zeit
    fireDatetime(screen.getByLabelText("Zeitpunkt"), "2030-01-02T18:30");
    await user.click(screen.getByRole("button", { name: "Planen" }));

    expect(await screen.findByText(/Geplant für .* in #chat/)).toBeInTheDocument();
    const call = backendRef.current.calls.find((c) => c.path.endsWith("/70/send"))!;
    expect(call.body).toEqual({ channel_id: "2", scheduled_at: new Date(2030, 0, 2, 18, 30).toISOString() });

    await user.click(screen.getByRole("button", { name: "Absagen" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Absagen" })).toBeNull());
    expect(screen.getByRole("button", { name: "Planen" })).toBeInTheDocument();
  });

  it("verlangt erst das Speichern, bevor gesendet wird", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));
    await user.selectOptions(screen.getByLabelText("Kanal"), "2");

    await user.type(screen.getByLabelText("Nachrichtentext"), "!");

    expect(screen.getByRole("button", { name: "Jetzt senden" })).toBeDisabled();
    expect(screen.getByText("Speichere zuerst deine Änderungen, dann kannst du senden.")).toBeInTheDocument();
  });

  it("passt eine gesendete Nachricht per Speichern an", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry({ state: "sent", channel_id: "2", message_id: "900" })];
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));

    await user.type(screen.getByLabelText("Nachrichtentext"), "!");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await screen.findByText("Gespeichert.");
    const call = backendRef.current.calls.find((c) => c.method === "PUT")!;
    expect((call.body as { content: string }).content).toBe("Hallo zusammen!");
  });

  it("zeigt den Fehler der gesendeten Nachricht (z. B. in Discord gelöscht)", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [
      entry({ state: "sent", channel_id: "2", error: "Die gesendete Nachricht wurde in Discord gelöscht." }),
    ];
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));

    expect(screen.getByRole("alert")).toHaveTextContent("in Discord gelöscht");
  });

  it("löscht nach Rückfrage und nicht ohne", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));

    await user.click(screen.getByRole("button", { name: "Löschen" }));
    expect(backendRef.current.calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Löschen" }));

    expect(await screen.findByText("Noch keine Ankündigungen.")).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("fragt vor dem Verlassen nach, wenn es ungespeicherte Änderungen gibt", async () => {
    const user = userEvent.setup();
    backendRef.current.announcements = [entry()];
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    mount(PAGE);
    await user.click(await screen.findByRole("button", { name: /Eröffnung/ }));
    await user.type(screen.getByLabelText("Nachrichtentext"), "!");

    await user.click(screen.getByRole("link", { name: "Änderungsprotokoll" }));

    expect(confirm).toHaveBeenCalled();
    expect(window.location.pathname).toBe(PAGE);
  });

  it("bleibt auf Englisch bedienbar", async () => {
    mount(PAGE, "en");
    expect(await screen.findByText("No announcements yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New announcement" })).toBeInTheDocument();
  });
});

function fireDatetime(input: HTMLElement, value: string): void {
  // userEvent kann `datetime-local` nicht tippen; der Wert wird wie vom Browser gesetzt.
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
