import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fixtures } from "../test/schemas";
import { BIG, GUILD, renderModule } from "../test/harness";
import { hexToInt, intToHex } from "./SchemaForm";

describe("SchemaForm: alle Module", () => {
  for (const module of Object.keys(fixtures)) {
    it(`${module}: zeigt die Standardwerte an, ohne etwas zu verändern`, () => {
      const { state } = renderModule(module);
      expect(state.changes).toBe(0);
      expect(state.current).toEqual(fixtures[module]!.defaults);
    });
  }
});

describe("SchemaForm: einfache Felder", () => {
  it("Schalter und Zahl schreiben den neuen Wert", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("starboard");

    await user.click(screen.getByLabelText("Aktiv"));
    const threshold = screen.getByLabelText("Schwelle");
    await user.clear(threshold);
    await user.type(threshold, "7");

    expect(state.current).toMatchObject({ enabled: true, threshold: 7 });
  });

  it("Zahlenfeld: Zwischenstände und Unsinn überschreiben den Wert nicht", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("starboard", { threshold: 5 });
    const threshold = screen.getByLabelText("Schwelle");

    await user.clear(threshold);
    expect(state.current["threshold"]).toBe(5); // leer: letzter gültiger Wert bleibt
    await user.type(threshold, "2.5");
    expect(state.current["threshold"]).toBe(2); // 2 war gültig, 2.5 ist keine ganze Zahl
    expect(threshold).toHaveAttribute("aria-invalid", "true");
  });

  it("Kanal wählen liefert die ID als String, 'keine' liefert null", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("starboard");
    const select = screen.getByLabelText("Kanal");

    await user.selectOptions(select, BIG);
    expect(state.current["channel_id"]).toBe(BIG);

    await user.selectOptions(select, "");
    expect(state.current["channel_id"]).toBeNull();
  });

  it("Kanäle erscheinen mit Präfix und Kategorie, @everyone nie als Rolle", () => {
    renderModule("welcome");
    const channel = screen.getByLabelText("Beitritts-Kanal");
    expect(within(channel).getByText("#chat (Allgemein)")).toBeInTheDocument();
    expect(within(channel).getByText("🔊 Lounge (Allgemein)")).toBeInTheDocument();
    const roles = screen.getByLabelText("Autorollen für Mitglieder 1".replace(" 1", ""));
    expect(within(roles).queryByText("@everyone")).not.toBeInTheDocument();
    expect(within(roles).getByText("Mod")).toBeInTheDocument();
  });

  it("eine gespeicherte, inzwischen gelöschte Kanal-ID bleibt sichtbar und erhalten", () => {
    const { state } = renderModule("starboard", { channel_id: "424242" });

    expect(screen.getByLabelText("Kanal")).toHaveValue("424242");
    expect(screen.getByText("Unbekannt (424242)")).toBeInTheDocument();
    expect(state.current["channel_id"]).toBe("424242");
  });

  it("Farbe: Farbwähler schreibt eine RGB-Zahl", () => {
    expect(intToHex(0x5865f2)).toBe("#5865f2");
    expect(intToHex(5)).toBe("#000005");
    expect(intToHex(-4)).toBe("#000000");
    expect(hexToInt("#ff0000")).toBe(16711680);
    const { state } = renderModule("general");
    const color = screen.getByLabelText("Farbe der Bot-Nachrichten") as HTMLInputElement;
    expect(color.value).toBe("#5865f2");
    // type=color kennt keine Tastatur; Wert direkt setzen wie der Browser-Dialog.
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(color, "#ff0000");
    color.dispatchEvent(new Event("input", { bubbles: true }));
    expect(state.current["embed_color"]).toBe(16711680);
  });

  it("mehrzeiliger Text: leer wird zu null (nullable)", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("welcome", { join_message: "Hallo {user}" });
    const text = screen.getByLabelText("Beitritts-Text");

    await user.clear(text);

    expect(state.current["join_message"]).toBeNull();
    await user.type(text, "Hi");
    expect(state.current["join_message"]).toBe("Hi");
  });

  it("Zahl oder null: Schalter setzt Startwert und löscht wieder", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("moderation");
    expect(state.current["warn_expiry_days"]).toBeNull();
    const row = screen.getByText("Verwarnungen verfallen nach (Tagen)").closest("div")!;

    await user.click(within(row).getByRole("checkbox"));
    expect(state.current["warn_expiry_days"]).toBe(1); // untere Grenze des Schemas

    await user.click(within(row).getByRole("checkbox"));
    expect(state.current["warn_expiry_days"]).toBeNull();
  });

  it("Auswahl zeigt übersetzte Beschriftungen", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("leveling");

    await user.selectOptions(screen.getByLabelText("Level-Up ankündigen"), "Per DM");

    expect(state.current["announce"]).toBe("dm");
  });
});

describe("SchemaForm: Listen", () => {
  it("Rollenliste: hinzufügen, nicht doppelt anbieten, entfernen", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("welcome");
    const select = () => screen.getByLabelText("Autorollen für Mitglieder");

    await user.selectOptions(select(), "10");
    expect(state.current["autoroles"]).toEqual(["10"]);
    expect(screen.getByRole("listitem")).toHaveTextContent("Mod");
    expect(within(select()).queryByRole("option", { name: "Mod" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Entfernen: Mod" }));
    expect(state.current["autoroles"]).toEqual([]);
  });

  it("Liste am Limit bietet kein Hinzufügen mehr an", () => {
    const ids = Array.from({ length: 10 }, (_, i) => String(1000 + i));
    renderModule("welcome", { autoroles: ids });

    expect(screen.getByText("Höchstens 10 Einträge.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Autorollen für Mitglieder")).not.toBeInTheDocument();
  });

  it("Objektliste: neuer Eintrag mit gültigem Startwert, Feld ändern, entfernen", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("moderation");

    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));
    expect(state.current["escalation"]).toEqual([{ warns: 1, action: "timeout", duration_seconds: null }]);

    await user.selectOptions(screen.getByLabelText("Aktion"), "Bann");
    expect(state.current["escalation"]).toEqual([{ warns: 1, action: "ban", duration_seconds: null }]);

    await user.click(screen.getByRole("button", { name: "Entfernen" }));
    expect(state.current["escalation"]).toEqual([]);
  });

  it("Textliste (Wörter mit Art und Muster) lässt sich ergänzen", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("automod");
    await user.click(screen.getByText("Wörter"));

    const words = screen.getByText("Wörter").closest("details")!;
    await user.click(within(words).getByRole("button", { name: "+ Hinzufügen" }));

    expect((state.current["words"] as { entries: unknown[] }).entries).toEqual([{ type: "word", pattern: "" }]);
  });
});

describe("SchemaForm: Zuordnungen", () => {
  it("Ereignis → Kanal: nur freie Ereignisse wählbar, Kanal setzen, entfernen", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("logging");
    const adder = () => screen.getByLabelText("Kanal pro Ereignis: Hinzufügen");

    await user.selectOptions(adder(), "Beitritt");
    expect(state.current["channels"]).toEqual({ member_join: "" });
    expect(within(adder()).queryByRole("option", { name: "Beitritt" })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Beitritt"), BIG);
    expect(state.current["channels"]).toEqual({ member_join: BIG });

    await user.click(screen.getByRole("button", { name: "Entfernen" }));
    expect(state.current["channels"]).toEqual({});
  });

  it("Level → Rolle: Level als Zahl eingeben, Doppelte und Leere ignorieren", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("leveling");
    const level = screen.getByLabelText("Rollen-Belohnungen: Level");
    const add = screen.getAllByRole("button", { name: "+ Hinzufügen" })[0]!;

    await user.click(add); // leer: nichts passiert
    expect(state.current["rewards"]).toEqual({});

    await user.type(level, "5");
    await user.click(add);
    expect(Object.keys(state.current["rewards"] as object)).toEqual(["5"]);

    await user.selectOptions(screen.getByLabelText("Level 5"), "10");
    expect(state.current["rewards"]).toEqual({ "5": "10" });
  });

  it("Rolle → Faktor: Rolle als Schlüssel", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("leveling");

    await user.selectOptions(screen.getByLabelText("XP-Faktor pro Rolle: Hinzufügen"), "Mod");

    expect(state.current["role_multipliers"]).toEqual({ "10": 1 });
  });
});

describe("SchemaForm: Fehler", () => {
  it("zeigt Serverfehler am Feld, auch in verschachtelten Objekten (klappt auf)", () => {
    renderModule("automod", {}, [{ loc: ["spam", "messages"], msg: "Input should be >= 2" }]);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Input should be >= 2");
    expect(alert.closest("details")).toHaveAttribute("open");
  });

  it("zeigt Fehler an Listeneinträgen und Zuordnungsschlüsseln", () => {
    renderModule(
      "moderation",
      { escalation: [{ warns: 1, action: "timeout", duration_seconds: null }] },
      [{ loc: ["escalation", 0, "duration_seconds"], msg: "timeout braucht eine Dauer" }],
    );
    expect(screen.getByRole("alert")).toHaveTextContent("timeout braucht eine Dauer");
  });

  it("Fehler ohne Ort stehen nicht im Feld (der Aufrufer zeigt sie oben)", () => {
    renderModule("starboard", {}, [{ loc: [], msg: "global" }]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("SchemaForm: grosse IDs", () => {
  it("IDs über 2^53 bleiben Strings und werden nie zur Zahl", async () => {
    const user = userEvent.setup();
    const { state } = renderModule("welcome");

    await user.selectOptions(screen.getByLabelText("Beitritts-Kanal"), BIG);
    await user.selectOptions(screen.getByLabelText("Autorollen für Mitglieder"), "10");

    expect(JSON.stringify(state.current)).toContain(`"join_channel_id":"${BIG}"`);
    expect(GUILD).toBe("100");
  });
});
