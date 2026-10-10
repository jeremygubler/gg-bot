import { useMe } from "../context";
import { useTr } from "../i18n";
import { Link, paths } from "../router";

export function inviteUrl(clientId: string, permissions: string, guildId: string): string {
  const query = new URLSearchParams({
    client_id: clientId,
    scope: "bot applications.commands",
    permissions,
    guild_id: guildId,
    disable_guild_select: "true",
  });
  return `https://discord.com/oauth2/authorize?${query.toString()}`;
}

export function iconUrl(id: string, icon: string): string {
  return `https://cdn.discordapp.com/icons/${id}/${icon}.png?size=64`;
}

export function ServersPage() {
  const me = useMe();
  const tr = useTr();
  // Server mit Bot zuerst; sonst bleibt die Reihenfolge von Discord.
  const guilds = [...me.guilds].sort((a, b) => Number(b.bot_present) - Number(a.bot_present));
  return (
    <section className="mx-auto max-w-3xl p-6">
      <h1 className="mb-4 text-2xl font-bold">{tr.t("servers.title")}</h1>
      {guilds.length === 0 && <p className="text-slate-400">{tr.t("servers.empty")}</p>}
      <ul className="space-y-2">
        {guilds.map((guild) => (
          <li key={guild.id} className="flex items-center gap-3 rounded border border-slate-700 bg-slate-800/50 p-3">
            {guild.icon ? (
              <img src={iconUrl(guild.id, guild.icon)} alt="" width={40} height={40} className="rounded-full" />
            ) : (
              <span aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-700">
                {guild.name.charAt(0).toUpperCase()}
              </span>
            )}
            <span className="min-w-0 flex-1 truncate font-medium">{guild.name}</span>
            {guild.bot_present ? (
              <Link to={paths.guild(guild.id)} className="rounded bg-indigo-600 px-3 py-1.5 text-sm hover:bg-indigo-500">
                {tr.t("servers.open")}
              </Link>
            ) : (
              <a
                href={inviteUrl(me.client_id, me.invite_permissions, guild.id)}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded border border-slate-500 px-3 py-1.5 text-sm hover:bg-slate-700"
                title={tr.t("servers.nobot")}
              >
                {tr.t("servers.invite")}
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
