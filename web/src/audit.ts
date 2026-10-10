import { equal, type Json, type JsonObject } from "./schema";

export type FieldChange = { key: string; before: Json | undefined; after: Json | undefined };

/** Felder der obersten Ebene, die sich zwischen zwei Ständen unterscheiden (ohne Vorgänger: alle). */
export function changedFields(before: JsonObject | null, after: JsonObject): FieldChange[] {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after)]);
  const changes: FieldChange[] = [];
  for (const key of [...keys].sort()) {
    const was = before?.[key];
    const now = after[key];
    if (before === null || was === undefined || now === undefined || !equal(was, now)) {
      changes.push({ key, before: was, after: now });
    }
  }
  return changes;
}

export function preview(value: Json | undefined, limit = 120): string {
  if (value === undefined) return "–";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}
