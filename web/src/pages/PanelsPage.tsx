import { useMe, useGuild } from "../context";
import { useTr } from "../i18n";
import { RolePanels } from "../panels/RolePanels";
import { TicketPanels } from "../panels/TicketPanels";
import type { PanelKind } from "../router";

export function PanelsPage({ kind }: { kind: PanelKind }) {
  const tr = useTr();
  const me = useMe();
  const { id } = useGuild();
  const administrator = me.guilds.find((g) => g.id === id)?.administrator === true;
  return (
    <section>
      <h1 className="mb-4 text-2xl font-bold">{tr.t(kind === "roles" ? "panels.roles.title" : "panels.tickets.title")}</h1>
      {kind === "roles" ? (
        <>
          {!administrator && (
            <p role="note" className="mb-4 rounded border border-amber-500/40 bg-amber-950/30 p-3 text-amber-200">
              {tr.t("panels.adminOnly")}
            </p>
          )}
          <RolePanels canEdit={administrator} />
        </>
      ) : (
        <TicketPanels />
      )}
    </section>
  );
}
