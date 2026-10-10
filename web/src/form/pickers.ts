import type { Snapshot } from "../api";
import type { PickerKind } from "../schema";

export type Option = { id: string; label: string };

const PREFIX: Record<string, string> = {
  text: "#",
  news: "#",
  forum: "#",
  voice: "🔊 ",
  stage_voice: "🎙 ",
  category: "▾ ",
};

/** Auswahl für Kanal- und Rollenfelder aus dem Snapshot des Bots, in Discords Reihenfolge. */
export function options(snapshot: Snapshot, kind: PickerKind): Option[] {
  if (kind === "channel") {
    const categories = new Map(snapshot.channels.filter((c) => c.type === "category").map((c) => [c.id, c.name]));
    return [...snapshot.channels]
      .sort((a, b) => a.position - b.position)
      .map((channel) => {
        const parent = channel.parent_id === null ? undefined : categories.get(channel.parent_id);
        const name = `${PREFIX[channel.type] ?? ""}${channel.name}`;
        return { id: channel.id, label: parent === undefined ? name : `${name} (${parent})` };
      });
  }
  // @everyone trägt die Server-ID und ist als Ziel (Autorolle, Ausnahme …) nie sinnvoll.
  return [...snapshot.roles]
    .filter((role) => role.id !== snapshot.guild_id)
    .sort((a, b) => b.position - a.position)
    .map((role) => ({ id: role.id, label: role.name }));
}

export function labelOf(list: Option[], id: string): string | undefined {
  return list.find((option) => option.id === id)?.label;
}
