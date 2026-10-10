import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backendRef, mount, setupBackend } from "./test/mount";

setupBackend();

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
});

const previewCalls = () => backendRef.current.calls.filter((c) => c.path.includes("/preview/"));

describe("Kartenvorschau", () => {
  it("Leveling zeigt die Rang-Karte mit dem Stil aus dem Formular", async () => {
    mount("/g/100/m/leveling");

    const image = await screen.findByRole("img", { name: "Rang-Karte (mit Beispieldaten)" });

    expect(image).toHaveAttribute("src", "blob:preview");
    expect(previewCalls()).toHaveLength(1);
    expect(previewCalls()[0]!.path).toBe("/api/guilds/100/preview/rank");
    expect(previewCalls()[0]!.body).toEqual({ accent: 0x5865f2, background: 0x23272a, template: "gradient" });
    expect(previewCalls()[0]!.csrf).toBe("ggbot");
  });

  it("Willkommen zeigt die Willkommens-Karte", async () => {
    mount("/g/100/m/welcome");

    await screen.findByRole("img", { name: "Willkommens-Karte (mit Beispieldaten)" });

    expect(previewCalls()[0]!.path).toBe("/api/guilds/100/preview/welcome");
  });

  it("Module ohne Karte haben keine Vorschau und stellen keine Anfrage", async () => {
    mount("/g/100/m/general");
    await screen.findByLabelText("Farbe der Bot-Nachrichten");

    expect(screen.queryByText(/Vorschau/)).not.toBeInTheDocument();
    expect(previewCalls()).toHaveLength(0);
  });

  it("folgt Änderungen am Formular, aber mit einer Anfrage pro Pause", async () => {
    mount("/g/100/m/leveling");
    await screen.findByRole("img", { name: /Rang-Karte/ });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const accent = screen.getByLabelText("Akzentfarbe") as HTMLInputElement;

    // Wie ein Ziehen am Farbwähler: viele Änderungen kurz hintereinander.
    for (const color of ["#ff0000", "#00ff00", "#0000ff"]) {
      fireEvent.input(accent, { target: { value: color } });
      await act(() => vi.advanceTimersByTimeAsync(100));
    }
    expect(previewCalls()).toHaveLength(1); // noch nichts nachgeschoben

    await act(() => vi.advanceTimersByTimeAsync(500));

    expect(previewCalls()).toHaveLength(2);
    expect((previewCalls()[1]!.body as { accent: number }).accent).toBe(0x0000ff);
  });

  it("zeigt einen Hinweis, wenn die Vorschau fehlschlägt, und das Formular bleibt benutzbar", async () => {
    backendRef.current.previewFails = true;
    const user = userEvent.setup();
    mount("/g/100/m/leveling");

    expect(await screen.findByText("Die Vorschau ist gerade nicht verfügbar.")).toBeInTheDocument();
    await user.click(screen.getAllByLabelText("Aktiv")[0]!);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
  });

  it("der Server bekommt die Sprache der Oberfläche", async () => {
    mount("/g/100/m/leveling", "en");

    await screen.findByRole("img", { name: /Rank card/ });

    expect(previewCalls()[0]!.locale).toBe("en");
  });

  it("zeigt den Link zur öffentlichen Seite nur bei aktivierter Freigabe", async () => {
    const user = userEvent.setup();
    mount("/g/100/m/leveling");
    await screen.findByLabelText("Öffentliche Rangliste");
    expect(screen.queryByText("Öffentliche Seite:")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Öffentliche Rangliste"));

    const link = screen.getByRole("link", { name: /\/leaderboard\/100$/ });
    expect(link).toHaveAttribute("href", "/leaderboard/100");
    expect(link.getAttribute("rel")).toContain("noopener");
  });
});
