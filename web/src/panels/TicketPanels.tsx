import { useState } from "react";
import type { Category, CategoryInput, TicketPanel } from "../api";
import { useApi, useGuild } from "../context";
import { useTr } from "../i18n";
import { ErrorBox, Spinner } from "../ui";
import { useAsync } from "../useAsync";
import {
  BUTTON,
  ErrorLine,
  PRIMARY,
  PublishBox,
  RoleChip,
  SelectInput,
  TextInput,
  categoryChannels,
  channelName,
  roleName,
  useAction,
} from "./shared";

export function supportRoleChoices(roles: { id: string; name: string; position: number }[], guildId: string, used: string[]) {
  return [...roles]
    .filter((role) => role.id !== guildId && !used.includes(role.id))
    .sort((a, b) => b.position - a.position)
    .map((role) => ({ value: role.id, label: role.name }));
}

export function TicketPanels() {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [loaded, reload] = useAsync(() => api.ticketPanels(id), [id]);
  if (loaded.status === "loading") return <Spinner />;
  if (loaded.status === "error") return <ErrorBox code={loaded.code} onRetry={reload} />;
  return (
    <div className="space-y-4">
      <CreateForm onCreated={reload} />
      {loaded.data.length === 0 && <p className="text-slate-400">{tr.t("panels.empty")}</p>}
      {loaded.data.map((panel) => (
        <PanelCard key={panel.id} panel={panel} reload={reload} />
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
  const action = useAction();
  const submit = async () => {
    if (await action.run(() => api.createTicketPanel(id, { title, description: description || null }))) {
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
        <button type="submit" className={PRIMARY} disabled={action.busy || title.trim() === ""}>
          {tr.t("panels.create")}
        </button>
        <ErrorLine message={action.error} />
      </form>
    </details>
  );
}

function PanelCard({ panel, reload }: { panel: TicketPanel; reload: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const [editing, setEditing] = useState(false);
  const action = useAction();

  const remove = async () => {
    if (!window.confirm(tr.t("panels.confirmDelete", { title: panel.title }))) return;
    if (await action.run(() => api.deleteTicketPanel(id, panel.id))) reload();
  };

  return (
    <section className="rounded border border-slate-700 bg-slate-800/40 p-3" aria-label={panel.title}>
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{panel.title}</h2>
        <span className="flex-1" />
        <button type="button" className={BUTTON} onClick={() => setEditing(!editing)}>
          {tr.t("panels.edit")}
        </button>
        <button type="button" className={BUTTON} onClick={() => void remove()} disabled={action.busy}>
          {tr.t("panels.delete")}
        </button>
      </header>
      {panel.description && <p className="mt-1 text-sm text-slate-400">{panel.description}</p>}
      <p className="mt-1 text-sm text-slate-400">
        {panel.published && panel.channel_id !== null
          ? tr.t("panels.published", { channel: channelName(snapshot, panel.channel_id, tr) })
          : tr.t("panels.unpublished")}
      </p>
      <ErrorLine message={action.error} />
      {editing && <EditForm panel={panel} onDone={() => { setEditing(false); reload(); }} />}
      <Categories panel={panel} reload={reload} />
      <PublishBox
        published={panel.published}
        publish={(channel) => api.publishTicketPanel(id, panel.id, channel)}
        status={() => api.ticketPanelStatus(id, panel.id)}
        onDone={reload}
      />
    </section>
  );
}

function EditForm({ panel, onDone }: { panel: TicketPanel; onDone: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [title, setTitle] = useState(panel.title);
  const [description, setDescription] = useState(panel.description ?? "");
  const action = useAction();
  const submit = async () => {
    if (await action.run(() => api.updateTicketPanel(id, panel.id, { title, description: description || null }))) onDone();
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
      <button type="submit" className={PRIMARY} disabled={action.busy || title.trim() === ""}>
        {tr.t("panels.save")}
      </button>
      <ErrorLine message={action.error} />
    </form>
  );
}

function Categories({ panel, reload }: { panel: TicketPanel; reload: () => void }) {
  const tr = useTr();
  return (
    <div className="mt-3">
      <h3 className="mb-1 text-sm font-medium text-slate-300">{tr.t("panels.categories")}</h3>
      {panel.categories.length === 0 && <p className="text-sm text-slate-500">{tr.t("panels.noCategories")}</p>}
      <ul className="space-y-2">
        {panel.categories.map((category) => (
          <CategoryRow key={category.id} panel={panel} category={category} reload={reload} />
        ))}
      </ul>
      <AddCategory panel={panel} onAdded={reload} />
    </div>
  );
}

function CategoryRow({ panel, category, reload }: { panel: TicketPanel; category: Category; reload: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const [role, setRole] = useState("");
  const action = useAction();
  const choices = supportRoleChoices(snapshot.roles, snapshot.guild_id, category.support_role_ids);

  const removeCategory = async () => {
    if (!window.confirm(tr.t("panels.confirmRemoveCategory", { name: category.name }))) return;
    if (await action.run(() => api.removeCategory(id, panel.id, category.id))) reload();
  };

  return (
    <li className="rounded border border-slate-700 p-2" aria-label={category.name}>
      <div className="flex items-center gap-2">
        <span className="flex-1 font-medium">
          {category.emoji} {category.name}
        </span>
        <button type="button" className={BUTTON} onClick={() => void removeCategory()} disabled={action.busy}>
          {tr.t("panels.delete")}
        </button>
      </div>
      {category.description && <p className="text-sm text-slate-400">{category.description}</p>}
      <ul className="mt-2 flex flex-wrap gap-2" aria-label={tr.t("panels.supportRoles")}>
        {category.support_role_ids.map((roleId) => (
          <RoleChip
            key={roleId}
            label={roleName(snapshot, roleId, tr)}
            removeLabel={`${tr.t("form.remove")}: ${roleName(snapshot, roleId, tr)}`}
            onRemove={() =>
              void action.run(() => api.removeSupportRole(id, panel.id, category.id, roleId)).then((ok) => ok && reload())
            }
          />
        ))}
      </ul>
      <div className="mt-2 flex items-end gap-2">
        <div className="flex-1">
          <SelectInput
            label={`${category.name}: ${tr.t("panels.addRole")}`}
            value={role}
            onChange={setRole}
            choices={choices}
            placeholder={tr.t("form.choose")}
          />
        </div>
        <button
          type="button"
          className={BUTTON}
          disabled={role === "" || action.busy}
          onClick={() =>
            void action.run(() => api.addSupportRole(id, panel.id, category.id, role)).then((ok) => {
              if (ok) {
                setRole("");
                reload();
              }
            })
          }
        >
          {tr.t("form.add")}
        </button>
      </div>
      <ErrorLine message={action.error} />
    </li>
  );
}

function AddCategory({ panel, onAdded }: { panel: TicketPanel; onAdded: () => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [emoji, setEmoji] = useState("");
  const [description, setDescription] = useState("");
  const [parent, setParent] = useState("");
  const [welcome, setWelcome] = useState("");
  const [questions, setQuestions] = useState("");
  const action = useAction();

  const submit = async () => {
    const input: CategoryInput = {
      name,
      support_role_id: role,
      emoji: emoji.trim() || null,
      description: description.trim() || null,
      parent_id: parent || null,
      welcome_message: welcome.trim() || null,
      questions: questions.split("\n").map((q) => q.trim()).filter((q) => q !== ""),
    };
    if (await action.run(() => api.addCategory(id, panel.id, input))) {
      setName("");
      setRole("");
      setEmoji("");
      setDescription("");
      setParent("");
      setWelcome("");
      setQuestions("");
      onAdded();
    }
  };

  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none text-sm text-indigo-300">{tr.t("panels.addCategory")}</summary>
      <form
        className="mt-2 space-y-3"
        aria-label={tr.t("panels.addCategory")}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput label={tr.t("panels.name")} value={name} onChange={setName} maxLength={80} />
          <TextInput label={tr.t("panels.emoji")} value={emoji} onChange={setEmoji} maxLength={64} />
        </div>
        <SelectInput
          label={tr.t("panels.supportRole")}
          value={role}
          onChange={setRole}
          choices={supportRoleChoices(snapshot.roles, snapshot.guild_id, [])}
          placeholder={tr.t("form.choose")}
        />
        <TextInput label={tr.t("panels.itemDescription").replace(/ \(.*\)$/, "")} value={description} onChange={setDescription} maxLength={100} />
        <SelectInput
          label={tr.t("panels.parent")}
          value={parent}
          onChange={setParent}
          choices={categoryChannels(snapshot)}
          placeholder={tr.t("panels.noParent")}
        />
        <TextInput label={tr.t("panels.welcome")} value={welcome} onChange={setWelcome} multiline maxLength={1000} hint={tr.t("panels.welcomeHint")} />
        <TextInput label={tr.t("panels.questions")} value={questions} onChange={setQuestions} multiline />
        <button type="submit" className={PRIMARY} disabled={action.busy || name.trim() === "" || role === ""}>
          {tr.t("panels.addCategory")}
        </button>
        <ErrorLine message={action.error} />
      </form>
    </details>
  );
}
