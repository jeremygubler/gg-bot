import { render } from "@testing-library/react";
import { useState } from "react";
import type { Snapshot } from "../api";
import { SchemaForm } from "../form/SchemaForm";
import { LocaleProvider } from "../i18n";
import { compile, indexErrors, type FieldError, type JsonObject } from "../schema";
import { fixtures } from "./schemas";

export const GUILD = "100";
export const BIG = "987654321098765432"; // > 2^53

export const snapshot: Snapshot = {
  guild_id: GUILD,
  name: "Alpha",
  channels: [
    { id: "1", name: "Allgemein", type: "category", position: 0, parent_id: null },
    { id: "2", name: "chat", type: "text", position: 1, parent_id: "1" },
    { id: BIG, name: "logs", type: "text", position: 2, parent_id: null },
    { id: "4", name: "Lounge", type: "voice", position: 3, parent_id: "1" },
  ],
  roles: [
    { id: GUILD, name: "@everyone", color: 0, position: 0, managed: false },
    { id: "10", name: "Mod", color: 0, position: 5, managed: false },
    { id: "11", name: "Member", color: 0, position: 1, managed: false },
  ],
  emojis: [],
};

/** Rendert das Formular eines echten Moduls mit Zustand und liefert den jeweils aktuellen Wert. */
export function renderModule(
  module: string,
  initial: Partial<JsonObject> = {},
  errors: FieldError[] = [],
) {
  const fixture = fixtures[module]!;
  const spec = compile(fixture.schema);
  const start = { ...fixture.defaults, ...initial } as JsonObject;
  const state = { current: start, changes: 0 };

  function Harness() {
    const [value, setValue] = useState<JsonObject>(start);
    return (
      <SchemaForm
        spec={spec}
        value={value}
        snapshot={snapshot}
        errors={indexErrors(errors)}
        onChange={(next) => {
          state.current = next;
          state.changes += 1;
          setValue(next);
        }}
      />
    );
  }

  const view = render(
    <LocaleProvider initial="de">
      <Harness />
    </LocaleProvider>,
  );
  return { ...view, state };
}
