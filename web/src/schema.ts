/**
 * Übersetzt das JSON-Schema eines Pydantic-Modells in eine kleine Formularbeschreibung.
 *
 * Absichtlich kein allgemeiner JSON-Schema-Interpreter: er versteht genau die Formen, die
 * die Einstellungsmodelle erzeugen (`$ref`, nullable über `anyOf`, Enums, Listen, Zuordnungen,
 * die `x-…`-Hinweise des Servers). Alles andere ist ein Fehler und wird beim Bauen des
 * Formulars gemeldet, statt stillschweigend ein Feld zu verlieren.
 */

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };

export type PickerKind = "channel" | "role";

export type KeySpec =
  | { kind: "text" }
  | { kind: "integer"; label?: string }
  | { kind: "enum"; values: string[] }
  | { kind: "picker"; picker: PickerKind };

export type Spec =
  | { kind: "boolean" }
  | { kind: "integer" | "number"; min?: number; max?: number }
  | { kind: "color" }
  | { kind: "text"; minLength?: number; maxLength?: number; multiline: boolean }
  | { kind: "enum"; values: string[] }
  | { kind: "picker"; picker: PickerKind }
  | { kind: "list"; item: Field; maxItems?: number }
  | { kind: "map"; key: KeySpec; value: Field; maxItems?: number }
  | { kind: "object"; fields: Field[] };

export type Field = {
  name: string;
  spec: Spec;
  nullable: boolean;
  default: Json | undefined;
  description?: string;
};

type Schema = {
  $ref?: string;
  $defs?: Record<string, Schema>;
  type?: string;
  anyOf?: Schema[];
  enum?: string[];
  properties?: Record<string, Schema>;
  items?: Schema;
  additionalProperties?: Schema | boolean;
  patternProperties?: Record<string, Schema>;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  maxItems?: number;
  maxProperties?: number;
  default?: Json;
  description?: string;
  [extension: `x-${string}`]: unknown;
};

export class SchemaError extends Error {}

function resolve(node: Schema, root: Schema): Schema {
  if (node.$ref === undefined) return node;
  const prefix = "#/$defs/";
  if (!node.$ref.startsWith(prefix)) throw new SchemaError(`Unbekannte Referenz ${node.$ref}`);
  const target = root.$defs?.[node.$ref.slice(prefix.length)];
  if (target === undefined) throw new SchemaError(`Referenz ${node.$ref} fehlt`);
  const { $ref: _ref, ...siblings } = node;
  return { ...resolve(target, root), ...siblings };
}

function unwrapNullable(node: Schema, root: Schema): { node: Schema; nullable: boolean } {
  const resolved = resolve(node, root);
  if (resolved.anyOf === undefined) return { node: resolved, nullable: false };
  const rest = resolved.anyOf.filter((option) => option.type !== "null");
  if (rest.length !== 1) throw new SchemaError("Nur „Wert oder null“ wird als anyOf unterstützt");
  const { anyOf: _any, ...outer } = resolved;
  return { node: { ...resolve(rest[0]!, root), ...outer }, nullable: rest.length !== resolved.anyOf.length };
}

function picker(node: Schema): PickerKind | undefined {
  const value = node["x-picker"];
  return value === "channel" || value === "role" ? value : undefined;
}

function keySpec(node: Schema): KeySpec {
  const keyPicker = node["x-key-picker"];
  if (keyPicker === "channel" || keyPicker === "role") return { kind: "picker", picker: keyPicker };
  const keys = node["x-keys"];
  if (Array.isArray(keys)) return { kind: "enum", values: keys.map(String) };
  if (node["x-key-type"] === "integer") {
    const label = node["x-key-label"];
    return { kind: "integer", label: typeof label === "string" ? label : undefined };
  }
  return { kind: "text" };
}

function buildSpec(node: Schema, root: Schema, where: string): Spec {
  const widget = node["x-widget"];
  const chosen = picker(node);
  if (chosen !== undefined) return { kind: "picker", picker: chosen };
  if (node.enum !== undefined) return { kind: "enum", values: node.enum };
  switch (node.type) {
    case "boolean":
      return { kind: "boolean" };
    case "integer":
      if (widget === "color") return { kind: "color" };
      return { kind: "integer", min: node.minimum, max: node.maximum };
    case "number":
      return { kind: "number", min: node.minimum, max: node.maximum };
    case "string":
      return {
        kind: "text",
        minLength: node.minLength,
        maxLength: node.maxLength,
        multiline: widget === "textarea",
      };
    case "array": {
      if (node.items === undefined) throw new SchemaError(`${where}: Liste ohne items`);
      return { kind: "list", item: buildField("item", node.items, root, `${where}[]`), maxItems: node.maxItems };
    }
    case "object": {
      if (node.properties !== undefined) {
        return {
          kind: "object",
          // `x-hidden`: Werte, die der Bot pflegt (z. B. die ID einer Panel-Nachricht). Sie bleiben im
          // Wert erhalten und gehen beim Speichern unverändert zurück, nur das Formular zeigt sie nicht.
          fields: Object.entries(node.properties)
            .filter(([, child]) => child["x-hidden"] !== true)
            .map(([name, child]) => buildField(name, child, root, `${where}.${name}`)),
        };
      }
      const valueSchema =
        typeof node.additionalProperties === "object"
          ? node.additionalProperties
          : Object.values(node.patternProperties ?? {})[0];
      if (valueSchema === undefined) throw new SchemaError(`${where}: Zuordnung ohne Wertschema`);
      return {
        kind: "map",
        key: keySpec(node),
        value: buildField("value", valueSchema, root, `${where}{}`),
        maxItems: node.maxProperties,
      };
    }
    default:
      throw new SchemaError(`${where}: nicht unterstützter Typ ${String(node.type)}`);
  }
}

function buildField(name: string, raw: Schema, root: Schema, where: string): Field {
  const { node, nullable } = unwrapNullable(raw, root);
  return {
    name,
    spec: buildSpec(node, root, where),
    nullable,
    default: node.default,
    description: node.description,
  };
}

/** Baut das Formular für ein ganzes Modul. */
export function compile(schema: object): Extract<Spec, { kind: "object" }> {
  const root = schema as Schema;
  const spec = buildSpec(resolve(root, root), root, "$");
  if (spec.kind !== "object") throw new SchemaError("Das Wurzelschema muss ein Objekt sein");
  return spec;
}

/** Startwert für ein neu hinzugefügtes Listenelement bzw. einen neuen Zuordnungseintrag. */
export function blank(field: Field): Json {
  if (field.default !== undefined && field.default !== null) return field.default;
  if (field.nullable) return null;
  const { spec } = field;
  switch (spec.kind) {
    case "boolean":
      return false;
    case "integer":
      return spec.min ?? 0;
    case "number":
      // Zahlen mit Komma sind hier Faktoren; 1 ändert nichts, 0 würde stillschweigend alles abschalten.
      return spec.min ?? 1;
    case "color":
      return 0;
    case "text":
    case "picker":
      return "";
    case "enum":
      return spec.values[0] ?? "";
    case "list":
      return [];
    case "map":
      return {};
    case "object":
      return Object.fromEntries(spec.fields.map((child) => [child.name, blank(child)]));
  }
}

export type FieldError = { loc: (string | number)[]; msg: string };

/** Pfad als Schlüssel, z. B. ["escalation", 0, "warns"] → "escalation.0.warns". */
export function pathKey(path: readonly (string | number)[]): string {
  return path.join(".");
}

export function indexErrors(errors: FieldError[]): Map<string, string[]> {
  const index = new Map<string, string[]>();
  for (const error of errors) {
    const key = pathKey(error.loc);
    index.set(key, [...(index.get(key) ?? []), cleanMessage(error.msg)]);
  }
  return index;
}

function cleanMessage(message: string): string {
  return message.replace(/^Value error, /, "");
}

/** Wert an einem Pfad unveränderlich ersetzen. */
export function setAt(value: Json, path: readonly (string | number)[], next: Json): Json {
  if (path.length === 0) return next;
  const [head, ...rest] = path as [string | number, ...(string | number)[]];
  if (Array.isArray(value)) {
    const copy = [...value];
    copy[Number(head)] = setAt(copy[Number(head)] ?? null, rest, next);
    return copy;
  }
  const object: JsonObject = value !== null && typeof value === "object" ? { ...value } : {};
  object[String(head)] = setAt(object[String(head)] ?? null, rest, next);
  return object;
}

export function equal(a: Json, b: Json): boolean {
  return JSON.stringify(sortKeys(a)) === JSON.stringify(sortKeys(b));
}

function sortKeys(value: Json): Json {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortKeys(value[key]!)]),
    );
  }
  return value;
}
