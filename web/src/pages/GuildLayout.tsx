import type { ReactNode } from "react";
import { GuildContext, useApi, useMe } from "../context";
import { useTr } from "../i18n";
import { Link, paths, useRoute } from "../router";
import { ErrorBox, Spinner } from "../ui";
import { useAsync } from "../useAsync";

export function GuildLayout({ guild, children }: { guild: string; children: ReactNode }) {
  const api = useApi();
  const tr = useTr();
  const me = useMe();
  const route = useRoute();
  const [snapshot, reloadSnapshot] = useAsync(() => api.snapshot(guild), [guild]);
  const [modules, reloadModules] = useAsync(() => api.modules(), []);

  if (snapshot.status === "error") {
    return (
      <ErrorBox code={snapshot.code} onRetry={reloadSnapshot}>
        <Link to={paths.home()} className="mt-3 block underline">
          {tr.t("nav.servers")}
        </Link>
      </ErrorBox>
    );
  }
  if (modules.status === "error") return <ErrorBox code={modules.code} onRetry={reloadModules} />;
  if (snapshot.status === "loading" || modules.status === "loading") return <Spinner />;

  const name = me.guilds.find((g) => g.id === guild)?.name ?? snapshot.data.name;
  const currentModule = route.name === "module" ? route.module : null;
  const onAudit = route.name === "audit";
  const onPanels = route.name === "panels" ? route.kind : null;
  const item = (active: boolean) =>
    `block rounded px-3 py-1.5 ${active ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-700"}`;

  return (
    <GuildContext.Provider value={{ id: guild, snapshot: snapshot.data }}>
      <div className="mx-auto flex max-w-6xl flex-col gap-4 p-4 md:flex-row">
        <nav aria-label={tr.t("nav.modules")} className="shrink-0 md:w-56">
          <p className="mb-2 truncate px-3 text-sm font-semibold text-slate-400">{name}</p>
          <ul className="space-y-1">
            {modules.data.map((module) => (
              <li key={module.module}>
                <Link
                  to={paths.module(guild, module.module)}
                  className={item(currentModule === module.module)}
                  current={currentModule === module.module}
                >
                  {tr.module(module.module)}
                </Link>
              </li>
            ))}
            <li className="pt-2">
              <Link to={paths.panels(guild, "roles")} className={item(onPanels === "roles")} current={onPanels === "roles"}>
                {tr.t("nav.rolepanels")}
              </Link>
            </li>
            <li>
              <Link to={paths.panels(guild, "tickets")} className={item(onPanels === "tickets")} current={onPanels === "tickets"}>
                {tr.t("nav.ticketpanels")}
              </Link>
            </li>
            <li className="pt-2">
              <Link to={paths.audit(guild)} className={item(onAudit)} current={onAudit}>
                {tr.t("nav.audit")}
              </Link>
            </li>
          </ul>
        </nav>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </GuildContext.Provider>
  );
}
