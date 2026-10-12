import { describe, expect, it } from "vitest";
import { fixtures } from "./test/schemas";
import { compile, type Spec } from "./schema";
import { humanize, makeTranslator, tables } from "./i18n";

function collect(spec: Spec, names: Set<string>, enums: Set<string>): void {
  if (spec.kind === "object") {
    for (const field of spec.fields) {
      names.add(field.name);
      collect(field.spec, names, enums);
    }
  } else if (spec.kind === "list") {
    collect(spec.item.spec, names, enums);
  } else if (spec.kind === "map") {
    if (spec.key.kind === "enum") spec.key.values.forEach((v) => enums.add(v));
    collect(spec.value.spec, names, enums);
  } else if (spec.kind === "enum") {
    spec.values.forEach((v) => enums.add(v));
  }
}

describe("Übersetzungen", () => {
  const names = new Set<string>();
  const enums = new Set<string>();
  for (const fixture of Object.values(fixtures)) collect(compile(fixture.schema), names, enums);
  names.delete("item");
  names.delete("value");

  it("jedes Formularfeld, jede Auswahl und jedes Modul hat deutsche und englische Beschriftung", () => {
    expect([...names].filter((n) => !(n in tables.fields))).toEqual([]);
    expect([...enums].filter((e) => !(e in tables.enums))).toEqual([]);
    expect(Object.keys(fixtures).filter((m) => !(m in tables.modules))).toEqual([]);
  });

  it("keine Übersetzung ist leer, und de und en sind verschieden belegt", () => {
    for (const table of Object.values(tables)) {
      for (const [key, [de, en]] of Object.entries(table)) {
        expect(de.trim(), key).not.toBe("");
        expect(en.trim(), key).not.toBe("");
      }
    }
  });

  it("jeder Fehlerschlüssel der API hat eine Meldung", () => {
    const codes = [
      "unauthenticated", "dashboard_disabled", "guild_not_found", "module_not_found", "csrf",
      "rate_limited", "discord_unavailable", "oauth_state", "invalid_settings", "network",
      "needs_administrator", "not_found", "board_unavailable", "user_error",
    ];
    const tr = makeTranslator("de");
    for (const code of codes) expect(tr.error(code), code).not.toBe(tr.t("err.unknown"));
    expect(tr.error("etwas_neues")).toBe(tr.t("err.unknown"));
  });

  it("ein Wert kann je Feld anders heissen, sonst gilt der allgemeine Eintrag", () => {
    const tr = makeTranslator("de");

    expect(tr.enumValue("button", "mode")).toBe("Ein Klick genügt");
    expect(tr.enumValue("button")).toBe("Buttons");
    expect(tr.enumValue("button", "kind")).toBe("Buttons"); // kein Sondereintrag: allgemein
  });

  it("ersetzt Platzhalter und fällt für Unbekanntes auf lesbare Namen zurück", () => {
    expect(makeTranslator("en").t("form.limit", { n: 5 })).toBe("At most 5 entries.");
    expect(makeTranslator("de").field("neues_feld")).toBe("Neues feld");
    expect(humanize("a_b_c")).toBe("A b c");
  });
});
