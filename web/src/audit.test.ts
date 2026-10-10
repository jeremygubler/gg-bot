import { describe, expect, it } from "vitest";
import { changedFields, preview } from "./audit";

describe("changedFields", () => {
  it("nennt nur geänderte Felder, sortiert", () => {
    const changes = changedFields({ b: 1, a: { x: 1, y: 2 }, c: [1] }, { b: 2, a: { y: 2, x: 1 }, c: [1, 2] });

    expect(changes).toEqual([
      { key: "b", before: 1, after: 2 },
      { key: "c", before: [1], after: [1, 2] },
    ]);
  });

  it("ohne Vorgänger zählt jedes Feld", () => {
    expect(changedFields(null, { a: 1, b: 2 }).map((c) => c.key)).toEqual(["a", "b"]);
  });

  it("erkennt neu hinzugekommene und entfernte Felder", () => {
    expect(changedFields({ a: 1 }, { b: 1 })).toEqual([
      { key: "a", before: 1, after: undefined },
      { key: "b", before: undefined, after: 1 },
    ]);
  });

  it("gleiche Stände ergeben keine Änderung", () => {
    expect(changedFields({ a: null }, { a: null })).toEqual([]);
  });
});

describe("preview", () => {
  it("kürzt lange Werte und zeigt Fehlendes als Strich", () => {
    expect(preview("x".repeat(200), 10)).toBe("xxxxxxxxxx…");
    expect(preview(undefined)).toBe("–");
    expect(preview({ a: 1 })).toBe('{"a":1}');
  });
});
