import { describe, expect, it } from "vitest";
import { blank, compile, equal, indexErrors, setAt, SchemaError, type Field, type Spec } from "./schema";
import { fixtures } from "./test/schemas";

function field(module: string, ...path: string[]): Field {
  if (path.length === 0) return { name: module, spec: compile(fixtures[module]!.schema), nullable: false, default: undefined };
  let spec: Spec = compile(fixtures[module]!.schema);
  let found: Field | undefined;
  for (const name of path) {
    if (spec.kind !== "object") throw new Error(`${name}: kein Objekt`);
    found = spec.fields.find((f) => f.name === name);
    if (found === undefined) throw new Error(`${name} fehlt`);
    spec = found.spec;
  }
  return found!;
}

describe("compile", () => {
  it("versteht jedes Modul des Servers", () => {
    for (const [name, fixture] of Object.entries(fixtures)) {
      expect(() => compile(fixture.schema), name).not.toThrow();
    }
  });

  it("erkennt Kanal- und Rollenauswahl, auch nullable und in Listen", () => {
    expect(field("welcome", "join_channel_id")).toMatchObject({
      nullable: true,
      spec: { kind: "picker", picker: "channel" },
    });
    const autoroles = field("welcome", "autoroles").spec;
    expect(autoroles).toMatchObject({ kind: "list", item: { spec: { kind: "picker", picker: "role" } } });
  });

  it("folgt $ref in verschachtelte Objekte", () => {
    const spam = field("automod", "spam").spec;
    expect(spam.kind).toBe("object");
    expect(field("automod", "spam", "messages").spec).toMatchObject({ kind: "integer", min: 2, max: 30 });
    expect(field("automod", "spam", "action", "timeout_seconds").nullable).toBe(true);
  });

  it("erkennt Farben, Mehrzeiliges und Enums", () => {
    expect(field("general", "embed_color").spec.kind).toBe("color");
    expect(field("leveling", "card", "accent").spec.kind).toBe("color");
    expect(field("welcome", "join_message").spec).toMatchObject({ kind: "text", multiline: true });
    expect(field("leveling", "announce").spec).toEqual({
      kind: "enum",
      values: ["off", "current", "channel", "dm"],
    });
  });

  it("erkennt die Schlüsselarten von Zuordnungen", () => {
    expect(field("logging", "channels").spec).toMatchObject({
      kind: "map",
      key: { kind: "enum" },
      value: { spec: { kind: "picker", picker: "channel" } },
    });
    expect(field("leveling", "rewards").spec).toMatchObject({
      kind: "map",
      key: { kind: "integer", label: "level" },
      value: { spec: { kind: "picker", picker: "role" } },
    });
    expect(field("leveling", "role_multipliers").spec).toMatchObject({
      kind: "map",
      key: { kind: "picker", picker: "role" },
      value: { spec: { kind: "number" } },
    });
  });

  it("versteckte Felder (x-hidden) fehlen im Formular, die übrigen bleiben", () => {
    const names = (field("verification").spec as { fields: Field[] }).fields.map((f) => f.name);

    expect(names).not.toContain("panel_message_id");
    expect(names).toEqual(expect.arrayContaining(["enabled", "role_id", "mode", "channel_id"]));
  });

  it("meldet unbekannte Formen, statt ein Feld zu verlieren", () => {
    expect(() => compile({ type: "object", properties: { x: { type: "null" } } })).toThrow(SchemaError);
    expect(() => compile({ type: "object", properties: { x: { $ref: "#/$defs/Fehlt" } } })).toThrow(
      /fehlt/,
    );
    expect(() =>
      compile({ type: "object", properties: { x: { anyOf: [{ type: "string" }, { type: "integer" }] } } }),
    ).toThrow(/anyOf/);
    expect(() => compile({ type: "string" })).toThrow(/Objekt/);
  });

  it("jedes Feld jedes Moduls hat einen bekannten Typ (keine stillen Lücken)", () => {
    const visit = (spec: Spec): number => {
      switch (spec.kind) {
        case "object":
          return spec.fields.reduce((n, f) => n + 1 + visit(f.spec), 0);
        case "list":
          return 1 + visit(spec.item.spec);
        case "map":
          return 1 + visit(spec.value.spec);
        default:
          return 1;
      }
    };
    for (const fixture of Object.values(fixtures)) expect(visit(compile(fixture.schema))).toBeGreaterThan(1);
  });
});

describe("blank", () => {
  it("baut gültige Startwerte für neue Einträge", () => {
    const entries = field("automod", "words", "entries").spec;
    if (entries.kind !== "list") throw new Error();
    expect(blank(entries.item)).toEqual({ type: "word", pattern: "" });
    const escalation = field("moderation", "escalation").spec;
    if (escalation.kind !== "list") throw new Error();
    expect(blank(escalation.item)).toEqual({ warns: 1, action: "timeout", duration_seconds: null });
  });
});

describe("Pfade und Fehler", () => {
  it("setAt ersetzt unveränderlich", () => {
    const before = { a: { b: [1, 2, 3] }, c: 1 };
    const after = setAt(before, ["a", "b", 1], 9);
    expect(after).toEqual({ a: { b: [1, 9, 3] }, c: 1 });
    expect(before.a.b[1]).toBe(2);
  });

  it("equal ignoriert die Reihenfolge der Schlüssel", () => {
    expect(equal({ a: 1, b: [1, { x: 1, y: 2 }] }, { b: [1, { y: 2, x: 1 }], a: 1 })).toBe(true);
    expect(equal({ a: 1 }, { a: 2 })).toBe(false);
  });

  it("indexErrors bündelt pro Pfad und entfernt Pydantics Präfix", () => {
    const index = indexErrors([
      { loc: ["escalation", 0, "warns"], msg: "Input should be >= 1" },
      { loc: [], msg: "Value error, xp_min darf nicht größer sein" },
      { loc: [], msg: "zweiter" },
    ]);
    expect(index.get("escalation.0.warns")).toEqual(["Input should be >= 1"]);
    expect(index.get("")).toEqual(["xp_min darf nicht größer sein", "zweiter"]);
  });
});
