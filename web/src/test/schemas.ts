import raw from "./schemas.json";
import type { JsonObject } from "../schema";

export type ModuleFixture = { schema: object; defaults: JsonObject };

/** Echte Schemas und Standardwerte, vom Server erzeugt (siehe tests/unit/test_web_schema_fixtures.py). */
export const fixtures = raw as unknown as Record<string, ModuleFixture>;
