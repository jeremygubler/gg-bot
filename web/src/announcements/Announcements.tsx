import { useEffect, useMemo, useState } from "react";
import type { Announcement, Embed, EmbedField } from "../api";
import { useApi, useGuild } from "../context";
import { useTr } from "../i18n";
import { useLeaveGuard } from "../router";
import { ErrorBox, Spinner } from "../ui";
import { useAsync } from "../useAsync";
import {
  BUTTON,
  Badge,
  ErrorLine,
  INPUT,
  Labeled,
  PRIMARY,
  SelectInput,
  TextInput,
  channelName,
  textChannels,
  useAction,
} from "../panels/shared";
import { MessagePreview, colorToHex, hexToColor } from "./EmbedPreview";

export const LIMITS = { name: 100, content: 2000, title: 256, description: 4096, author: 256, footer: 2048, fields: 25, fieldName: 256, fieldValue: 1024, url: 2000 };
const POLL_MS = 2000;
const MAX_POLLS = 30;

type Draft = {
  name: string;
  content: string;
  title: string;
  description: string;
  color: number | null;
  author: string;
  footer: string;
  imageUrl: string;
  thumbnailUrl: string;
  fields: EmbedField[];
};

const EMPTY: Draft = {
  name: "",
  content: "",
  title: "",
  description: "",
  color: null,
  author: "",
  footer: "",
  imageUrl: "",
  thumbnailUrl: "",
  fields: [],
};

export function toDraft(entry: Announcement | null): Draft {
  if (entry === null) return EMPTY;
  const embed = entry.embed;
  return {
    name: entry.name,
    content: entry.content ?? "",
    title: embed?.title ?? "",
    description: embed?.description ?? "",
    color: embed?.color ?? null,
    author: embed?.author ?? "",
    footer: embed?.footer ?? "",
    imageUrl: embed?.image_url ?? "",
    thumbnailUrl: embed?.thumbnail_url ?? "",
    fields: embed?.fields ?? [],
  };
}

const orNull = (value: string): string | null => (value.trim() === "" ? null : value);

/** Das Embed, wie es der Server bekommt; ohne jeden Inhalt `null` (eine Farbe allein ist kein Embed). */
export function toEmbed(draft: Draft): Embed | null {
  const embed: Embed = {
    title: orNull(draft.title),
    description: orNull(draft.description),
    color: draft.color,
    author: orNull(draft.author),
    footer: orNull(draft.footer),
    image_url: orNull(draft.imageUrl),
    thumbnail_url: orNull(draft.thumbnailUrl),
    fields: draft.fields,
  };
  const empty =
    !embed.title && !embed.description && !embed.author && !embed.footer && !embed.image_url && !embed.thumbnail_url && embed.fields.length === 0;
  return empty ? null : embed;
}

export function Announcements({ pollMs = POLL_MS }: { pollMs?: number }) {
  const api = useApi();
  const { id } = useGuild();
  const [loaded, reload] = useAsync(() => api.announcements(id), [id]);
  if (loaded.status === "loading") return <Spinner />;
  if (loaded.status === "error") return <ErrorBox code={loaded.code} onRetry={reload} />;
  return <Workspace initial={loaded.data} pollMs={pollMs} />;
}

/** Hält die Liste selbst: ein Neuladen würde den Editor abbauen und ungespeicherte Eingaben verwerfen. */
function Workspace({ initial, pollMs }: { initial: Announcement[]; pollMs: number }) {
  const tr = useTr();
  const [items, setItems] = useState(initial);
  const [selected, setSelected] = useState<string | "new" | null>(null);
  // Der Editor wird nur bei einer Auswahl neu aufgebaut, nicht wenn „neu“ zur gespeicherten wird.
  const [editorKey, setEditorKey] = useState(0);
  const choose = (next: string | "new" | null) => {
    setSelected(next);
    setEditorKey((k) => k + 1);
  };
  const entry = selected === null || selected === "new" ? null : (items.find((a) => a.id === selected) ?? null);
  const upsert = (next: Announcement) =>
    setItems((list) => (list.some((a) => a.id === next.id) ? list.map((a) => (a.id === next.id ? next : a)) : [next, ...list]));

  return (
    <div className="space-y-4">
      <p className="text-slate-400">{tr.t("ann.intro")}</p>
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <nav aria-label={tr.t("ann.list")}>
          <button type="button" className={`${PRIMARY} mb-2 w-full`} onClick={() => choose("new")}>
            {tr.t("ann.new")}
          </button>
          {items.length === 0 && <p className="text-sm text-slate-400">{tr.t("ann.empty")}</p>}
          <ul className="space-y-1">
            {items.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  aria-current={selected === a.id ? "true" : undefined}
                  onClick={() => choose(a.id)}
                  className={`flex w-full items-center justify-between gap-2 rounded px-3 py-1.5 text-left ${selected === a.id ? "bg-indigo-600" : "bg-slate-800 hover:bg-slate-700"}`}
                >
                  <span className="truncate">{a.name}</span>
                  <Badge>{tr.t(`ann.state.${a.state}`)}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </nav>
        {selected !== null && (
          <Editor
            key={editorKey}
            entry={entry}
            pollMs={pollMs}
            onSaved={(saved) => {
              upsert(saved);
              setSelected(saved.id); // Schlüssel bleibt: der Editor behält seinen Zustand
            }}
            onChanged={upsert}
            onDeleted={(deleted) => {
              setItems((list) => list.filter((a) => a.id !== deleted));
              choose(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

function Editor(props: {
  entry: Announcement | null;
  pollMs: number;
  onSaved: (entry: Announcement) => void;
  onChanged: (entry: Announcement) => void;
  onDeleted: (id: string) => void;
}) {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [current, setCurrent] = useState(props.entry);
  const saved = useMemo(() => toDraft(current), [current]);
  const [draft, setDraft] = useState<Draft>(saved);
  const [justSaved, setJustSaved] = useState(false);
  const action = useAction();
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  useLeaveGuard(dirty, tr.t("form.confirmLeave"));

  const patch = (change: Partial<Draft>) => {
    setJustSaved(false);
    setDraft((d) => ({ ...d, ...change }));
  };
  const embed = toEmbed(draft);

  const save = async () => {
    const input = { name: draft.name, content: orNull(draft.content), embed };
    let result: Announcement | null = null;
    const ok = await action.run(async () => {
      result = current === null ? await api.createAnnouncement(id, input) : await api.updateAnnouncement(id, current.id, input);
    });
    if (ok && result !== null) {
      const next: Announcement = result;
      setCurrent(next);
      setDraft(toDraft(next));
      setJustSaved(true);
      if (current === null) props.onSaved(next);
      else props.onChanged(next);
    }
  };

  const remove = async () => {
    if (current === null) return;
    if (!window.confirm(tr.t("ann.confirmDelete", { name: current.name }))) return;
    if (await action.run(() => api.deleteAnnouncement(id, current.id))) props.onDeleted(current.id);
  };

  // Eine fällige Ankündigung wird vom Bot innerhalb von Sekunden gesendet: dem Ergebnis nachfragen.
  useEffect(() => {
    if (current === null || current.state !== "scheduled" || current.scheduled_at === null) return undefined;
    if (Date.parse(current.scheduled_at) > Date.now() + 10_000) return undefined;
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      api.announcement(id, current.id).then(
        (fresh) => {
          if (fresh.state !== "scheduled") {
            setCurrent(fresh);
            props.onChanged(fresh);
          }
        },
        () => undefined,
      );
      if (tries >= MAX_POLLS) clearInterval(timer);
    }, props.pollMs);
    return () => clearInterval(timer);
  }, [current, api, id, props.pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="grid gap-4 xl:grid-cols-2" aria-label={current?.name ?? tr.t("ann.new")}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <TextInput label={tr.t("ann.name")} value={draft.name} onChange={(name) => patch({ name })} maxLength={LIMITS.name} />
        <TextInput
          label={tr.t("ann.content")}
          value={draft.content}
          onChange={(content) => patch({ content })}
          maxLength={LIMITS.content}
          multiline
          hint={tr.t("ann.chars", { n: draft.content.length, max: LIMITS.content })}
        />
        <fieldset className="space-y-3 rounded border border-slate-700 p-3">
          <legend className="px-1 text-sm font-medium">{tr.t("ann.embed")}</legend>
          <TextInput label={tr.t("ann.embedTitle")} value={draft.title} onChange={(title) => patch({ title })} maxLength={LIMITS.title} />
          <TextInput
            label={tr.t("ann.embedDescription")}
            value={draft.description}
            onChange={(description) => patch({ description })}
            maxLength={LIMITS.description}
            multiline
          />
          <ColorField value={draft.color} onChange={(color) => patch({ color })} />
          <TextInput label={tr.t("ann.author")} value={draft.author} onChange={(author) => patch({ author })} maxLength={LIMITS.author} />
          <TextInput label={tr.t("ann.footer")} value={draft.footer} onChange={(footer) => patch({ footer })} maxLength={LIMITS.footer} />
          <TextInput
            label={tr.t("ann.image")}
            value={draft.imageUrl}
            onChange={(imageUrl) => patch({ imageUrl })}
            maxLength={LIMITS.url}
            hint={tr.t("ann.urlHint")}
          />
          <TextInput
            label={tr.t("ann.thumbnail")}
            value={draft.thumbnailUrl}
            onChange={(thumbnailUrl) => patch({ thumbnailUrl })}
            maxLength={LIMITS.url}
          />
          <FieldsEditor fields={draft.fields} onChange={(fields) => patch({ fields })} />
        </fieldset>
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className={PRIMARY} disabled={action.busy || draft.name.trim() === ""}>
            {tr.t("ann.save")}
          </button>
          {current !== null && (
            <button type="button" className={BUTTON} disabled={action.busy} onClick={() => void remove()}>
              {tr.t("ann.delete")}
            </button>
          )}
          {justSaved && !dirty && <span role="status" className="text-sm text-emerald-400">{tr.t("ann.saved")}</span>}
          {dirty && <span className="text-sm text-amber-300">{tr.t("form.unsaved")}</span>}
        </div>
        <ErrorLine message={action.error} />
      </form>

      <div className="space-y-4">
        <MessagePreview content={draft.content} embed={embed} />
        {current !== null && (
          <SendBox
            entry={current}
            dirty={dirty}
            onChange={(next) => {
              setCurrent(next);
              props.onChanged(next);
            }}
          />
        )}
      </div>
    </section>
  );
}

function ColorField({ value, onChange }: { value: number | null; onChange: (color: number | null) => void }) {
  const tr = useTr();
  return (
    <Labeled label={tr.t("ann.color")}>
      {(id) => (
        <div className="flex items-center gap-2">
          <input
            id={id}
            type="color"
            value={colorToHex(value)}
            onChange={(e) => onChange(hexToColor(e.target.value))}
            className="h-9 w-16 rounded border border-slate-600 bg-slate-800"
          />
          {value !== null && (
            <button type="button" className={BUTTON} onClick={() => onChange(null)}>
              {tr.t("ann.noColor")}
            </button>
          )}
        </div>
      )}
    </Labeled>
  );
}

function FieldsEditor({ fields, onChange }: { fields: EmbedField[]; onChange: (fields: EmbedField[]) => void }) {
  const tr = useTr();
  const set = (index: number, change: Partial<EmbedField>) =>
    onChange(fields.map((f, i) => (i === index ? { ...f, ...change } : f)));
  return (
    <div className="space-y-2">
      <p className="text-sm text-slate-300">{tr.t("ann.fields")}</p>
      {fields.map((field, index) => (
        <div key={index} className="space-y-2 rounded border border-slate-700 p-2">
          <TextInput label={tr.t("ann.fieldName")} value={field.name} onChange={(name) => set(index, { name })} maxLength={LIMITS.fieldName} />
          <TextInput
            label={tr.t("ann.fieldValue")}
            value={field.value}
            onChange={(value) => set(index, { value })}
            maxLength={LIMITS.fieldValue}
            multiline
          />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={field.inline} onChange={(e) => set(index, { inline: e.target.checked })} />
            {tr.t("ann.fieldInline")}
          </label>
          <button type="button" className={BUTTON} onClick={() => onChange(fields.filter((_, i) => i !== index))}>
            {tr.t("ann.removeField")}
          </button>
        </div>
      ))}
      <button
        type="button"
        className={BUTTON}
        disabled={fields.length >= LIMITS.fields}
        onClick={() => onChange([...fields, { name: "", value: "", inline: false }])}
      >
        {tr.t("ann.addField")}
      </button>
    </div>
  );
}

/** `datetime-local` hat keine Zeitzone; der Browser deutet den Wert in der eigenen. */
export function localToIso(value: string): string {
  return new Date(value).toISOString();
}

function SendBox({ entry, dirty, onChange }: { entry: Announcement; dirty: boolean; onChange: (entry: Announcement) => void }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const channels = textChannels(snapshot);
  const [channel, setChannel] = useState(entry.channel_id ?? "");
  const [mode, setMode] = useState<"now" | "later">("now");
  const [when, setWhen] = useState("");
  const action = useAction();

  const send = async () => {
    let result: Announcement | null = null;
    const ok = await action.run(async () => {
      result = await api.sendAnnouncement(id, entry.id, channel, mode === "later" ? localToIso(when) : null);
    });
    if (ok && result !== null) onChange(result);
  };
  const cancel = async () => {
    let result: Announcement | null = null;
    const ok = await action.run(async () => {
      result = await api.cancelAnnouncement(id, entry.id);
    });
    if (ok && result !== null) onChange(result);
  };

  const where = entry.channel_id === null ? "" : channelName(snapshot, entry.channel_id, tr);
  return (
    <div className="space-y-3 rounded border border-slate-700 p-3">
      <h2 className="font-medium">{tr.t("ann.send")}</h2>
      <p className="flex flex-wrap items-center gap-2 text-sm" role="status">
        <Badge>{tr.t(`ann.state.${entry.state}`)}</Badge>
        {entry.state === "sent" && <span>{tr.t("ann.sentIn", { channel: where })}</span>}
        {entry.state === "scheduled" && entry.scheduled_at !== null && (
          <span>{tr.t("ann.scheduledFor", { time: new Date(entry.scheduled_at).toLocaleString(tr.locale), channel: where })}</span>
        )}
      </p>
      {entry.error !== null && <p role="alert" className="text-sm text-red-300">{entry.error}</p>}
      {entry.state === "sent" && <p className="text-sm text-slate-400">{tr.t("ann.editNote")}</p>}
      {entry.state === "scheduled" && (
        <button type="button" className={BUTTON} disabled={action.busy} onClick={() => void cancel()}>
          {tr.t("ann.cancel")}
        </button>
      )}
      {entry.state !== "sent" && (
        <div className="space-y-3">
          <SelectInput label={tr.t("ann.channel")} value={channel} onChange={setChannel} choices={channels} placeholder={tr.t("form.choose")} />
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-1">
              <input type="radio" name={`mode-${entry.id}`} checked={mode === "now"} onChange={() => setMode("now")} />
              {tr.t("ann.now")}
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" name={`mode-${entry.id}`} checked={mode === "later"} onChange={() => setMode("later")} />
              {tr.t("ann.later")}
            </label>
          </div>
          {mode === "later" && (
            <Labeled label={tr.t("ann.when")}>
              {(inputId) => <input id={inputId} type="datetime-local" className={INPUT} value={when} onChange={(e) => setWhen(e.target.value)} />}
            </Labeled>
          )}
          {dirty && <p className="text-sm text-amber-300">{tr.t("ann.saveFirst")}</p>}
          <button
            type="button"
            className={PRIMARY}
            disabled={action.busy || dirty || channel === "" || (mode === "later" && when === "")}
            onClick={() => void send()}
          >
            {mode === "later" ? tr.t("ann.schedule") : entry.state === "failed" ? tr.t("ann.resend") : tr.t("ann.sendNow")}
          </button>
        </div>
      )}
      <ErrorLine message={action.error} />
    </div>
  );
}
