import { useState } from "react";
import type { RoleItemInput, RolePanel, Snapshot } from "../api";
import { useApi, useGuild } from "../context";
import { useTr } from "../i18n";
import { ErrorBox, Spinner } from "../ui";
import { useAsync } from "../useAsync";
import {
  BUTTON,
  Badge,
  ErrorLine,
  PRIMARY,
  PublishBox,
  SelectInput,
  TextInput,
  channelName,
  roleName,
  useAction,
} from "./shared";

const KINDS = ["button", "select", "reaction"] as const;
const MODES = ["normal", "unique", "verify"] as const;

/** Rollen, die ein Panel anbieten kann: nicht @everyone, nicht verwaltet, unter der Rolle des Bots. */
export function grantableRoles(snapshot: Snapshot, used: string[]): { value: string; label: string }[] {
  return [...snapshot.roles]
    .filter(
      (role) =>
        role.id !== snapshot.guild_id &&
        !role.managed &&
        role.position < snapshot.bot_top_position &&
        !used.includes(role.id),
    )
    .sort((a, b) => b.position - a.position)
    .map((role) => ({ value: role.id, label: role.name }));
}

export function RolePanels({ canEdit }: { canEdit: boolean }) {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [loaded, reload] = useAsync(() => api.rolePanels(id), [id]);
  if (loaded.status === "loading") return <Spinner />;
  if (loaded.status === "error") return <ErrorBox code={loaded.code} onRetry={reload} />;
  return (
    <div className="space-y-4">
      {canEdit && <CreateForm onCreated={reload} />}
      {loaded.data.length === 0 && <p className="text-slate-400">{tr.t("panels.empty")}</p>}
      {loaded.data.map((panel) => (
        <PanelCard key={panel.id} panel={panel} canEdit={canEdit} reload={reload} />
      ))}
    </div>
  );
}

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<RolePanel["kind"]>("button");
  const [mode, setMode] = useState<RolePanel["mode"]>("normal");
  const action = useAction();

  const submit = async () => {
    const ok = await action.run(() =>
      api.createRolePanel(id, { title, description: description || null, kind, mode }),
    );
    if (ok) {
      setTitle("");
      setDescription("");
      onCreated();
    }
  };

  return (
    <details className="rounded border border-slate-700 bg-slate-800/40">
      <summary className="cursor-pointer select-none px-3 py-2 font-medium">{tr.t("panels.new")}</summary>
      <form
        className="space-y-3 border-t border-slate-700 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <TextInput label={tr.t("panels.title")} value={title} onChange={setTitle} maxLength={256} />
        <TextInput label={tr.t("panels.description")} value={description} onChange={setDescription} multiline maxLength={1000} />
        <div className="grid gap-3 sm:grid-cols-2">
          <SelectInput
            label={tr.t("panels.kind")}
            value={kind}
            onChange={(v) => setKind(v as RolePanel["kind"])}
            choices={KINDS.map((k) => ({ value: k, label: tr.enumValue(k) }))}
          />
          <SelectInput
            label={tr.t("panels.mode")}
            value={mode}
            onChange={(v) => setMode(v as RolePanel["mode"])}
            choices={MODES.map((m) => ({ value: m, label: tr.enumValue(m) }))}
          />
        </div>
        <button type="submit" className={PRIMARY} disabled={action.busy || title.trim() === ""}>
          {tr.t("panels.create")}
        </button>
        <ErrorLine message={action.error} />
      </form>
    </details>
  );
}

function PanelCard(props: { panel: RolePanel; canEdit: boolean; reload: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const { panel, canEdit, reload } = props;
  const [editing, setEditing] = useState(false);
  const action = useAction();

  const remove = async () => {
    if (!window.confirm(tr.t("panels.confirmDelete", { title: panel.title }))) return;
    if (await action.run(() => api.deleteRolePanel(id, panel.id))) reload();
  };

  return (
    <section className="rounded border border-slate-700 bg-slate-800/40 p-3" aria-label={panel.title}>
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{panel.title}</h2>
        <Badge>{tr.enumValue(panel.kind)}</Badge>
        <Badge>{tr.enumValue(panel.mode)}</Badge>
        <span className="flex-1" />
        {canEdit && (
          <>
            <button type="button" className={BUTTON} onClick={() => setEditing(!editing)}>
              {tr.t("panels.edit")}
            </button>
            <button type="button" className={BUTTON} onClick={() => void remove()} disabled={action.busy}>
              {tr.t("panels.delete")}
            </button>
          </>
        )}
      </header>
      {panel.description && <p className="mt-1 text-sm text-slate-400">{panel.description}</p>}
      <p className="mt-1 text-sm text-slate-400">
        {panel.published && panel.channel_id !== null
          ? tr.t("panels.published", { channel: channelName(snapshot, panel.channel_id, tr) })
          : tr.t("panels.unpublished")}
        {panel.external && ` · ${tr.t("panels.external")}`}
      </p>
      <ErrorLine message={action.error} />
      {editing && canEdit && <EditForm panel={panel} onDone={() => { setEditing(false); reload(); }} />}
      <Items panel={panel} canEdit={canEdit} reload={reload} />
      {canEdit && !panel.external && (
        <PublishBox
          published={panel.published}
          publish={(channel) => api.publishRolePanel(id, panel.id, channel)}
          status={() => api.rolePanelStatus(id, panel.id)}
          onDone={reload}
        />
      )}
    </section>
  );
}

function EditForm({ panel, onDone }: { panel: RolePanel; onDone: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [title, setTitle] = useState(panel.title);
  const [description, setDescription] = useState(panel.description ?? "");
  const [mode, setMode] = useState<RolePanel["mode"]>(panel.mode);
  const action = useAction();
  const submit = async () => {
    if (await action.run(() => api.updateRolePanel(id, panel.id, { title, description: description || null, mode }))) {
      onDone();
    }
  };
  return (
    <form
      className="mt-3 space-y-3 rounded border border-slate-700 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <TextInput label={tr.t("panels.title")} value={title} onChange={setTitle} maxLength={256} />
      <TextInput label={tr.t("panels.description")} value={description} onChange={setDescription} multiline maxLength={1000} />
      <SelectInput
        label={tr.t("panels.mode")}
        value={mode}
        onChange={(v) => setMode(v as RolePanel["mode"])}
        choices={MODES.map((m) => ({ value: m, label: tr.enumValue(m) }))}
      />
      <button type="submit" className={PRIMARY} disabled={action.busy || title.trim() === ""}>
        {tr.t("panels.save")}
      </button>
      <ErrorLine message={action.error} />
    </form>
  );
}

function Items({ panel, canEdit, reload }: { panel: RolePanel; canEdit: boolean; reload: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const action = useAction();
  return (
    <div className="mt-3">
      <h3 className="mb-1 text-sm font-medium text-slate-300">{tr.t("panels.items")}</h3>
      {panel.items.length === 0 && <p className="text-sm text-slate-500">{tr.t("panels.noItems")}</p>}
      <ul className="space-y-1">
        {panel.items.map((item) => (
          <li key={item.id} className="flex items-center gap-2 text-sm">
            <span className="min-w-0 flex-1 truncate">
              {item.emoji} {item.label} <span className="text-slate-500">→ {roleName(snapshot, item.role_id, tr)}</span>
            </span>
            {canEdit && (
              <button
                type="button"
                className="rounded px-2 py-1 text-slate-400 hover:text-red-300"
                aria-label={`${tr.t("form.remove")}: ${item.label ?? item.emoji ?? roleName(snapshot, item.role_id, tr)}`}
                disabled={action.busy}
                onClick={() => void action.run(() => api.removeRoleItem(id, panel.id, item.role_id)).then((ok) => ok && reload())}
              >
                ✕
              </button>
            )}
          </li>
        ))}
      </ul>
      <ErrorLine message={action.error} />
      {canEdit && !panel.external && <AddItem panel={panel} onAdded={reload} />}
    </div>
  );
}

function AddItem({ panel, onAdded }: { panel: RolePanel; onAdded: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const [role, setRole] = useState("");
  const [label, setLabel] = useState("");
  const [emoji, setEmoji] = useState("");
  const [description, setDescription] = useState("");
  const action = useAction();
  const choices = grantableRoles(snapshot, panel.items.map((i) => i.role_id));

  const submit = async () => {
    const input: RoleItemInput = {
      role_id: role,
      label: label.trim() || null,
      emoji: emoji.trim() || null,
      description: description.trim() || null,
    };
    if (await action.run(() => api.addRoleItem(id, panel.id, input))) {
      setRole("");
      setLabel("");
      setEmoji("");
      setDescription("");
      onAdded();
    }
  };

  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none text-sm text-indigo-300">{tr.t("panels.addItem")}</summary>
      <form
        className="mt-2 space-y-3"
        aria-label={tr.t("panels.addItem")}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <SelectInput label={tr.t("panels.role")} value={role} onChange={setRole} choices={choices} placeholder={tr.t("form.choose")} />
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput label={tr.t("panels.label")} value={label} onChange={setLabel} maxLength={80} />
          <TextInput label={tr.t("panels.emoji")} value={emoji} onChange={setEmoji} maxLength={64} />
        </div>
        {panel.kind === "select" && (
          <TextInput label={tr.t("panels.itemDescription")} value={description} onChange={setDescription} maxLength={100} />
        )}
        <button type="submit" className={PRIMARY} disabled={action.busy || role === ""}>
          {tr.t("panels.addItem")}
        </button>
        <ErrorLine message={action.error} />
      </form>
    </details>
  );
}
