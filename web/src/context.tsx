import { createContext, useContext } from "react";
import type { Api, Me, Snapshot } from "./api";

export const ApiContext = createContext<Api | null>(null);

export function useApi(): Api {
  const api = useContext(ApiContext);
  if (api === null) throw new Error("ApiContext fehlt");
  return api;
}

export const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (me === null) throw new Error("MeContext fehlt");
  return me;
}

export type GuildData = { id: string; snapshot: Snapshot };
export const GuildContext = createContext<GuildData | null>(null);

export function useGuild(): GuildData {
  const guild = useContext(GuildContext);
  if (guild === null) throw new Error("GuildContext fehlt");
  return guild;
}
