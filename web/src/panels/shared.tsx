import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError, type PublishStatus, type Snapshot } from "../api";
import { useGuild } from "../context";
import { options } from "../form/pickers";
import { useTr, type Translator } from "../i18n";

export const INPUT =
  "w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-slate-100 focus:border-indigo-400 focus:outline-none";
export const BUTTON = "rounded bg-slate-700 px-3 py-1.5 text-sm hover:bg-slate-600 disabled:opacity-40";
export const PRIMARY = "rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500 disabled:opacity-40";

/** Text für einen Fehler: die Meldung des Servers bei `user_error`, sonst die übersetzte zum Schlüssel. */
export function errorText(tr: Translator, error: unknown): string {
  if (error instanceof ApiError) {
    return error.code === "user_error" && error.userMessage !== null ? error.userMessage : tr.error(error.code);
  }
  return tr.error("unknown");
}

/** Führt Aktionen aus, merkt sich „läuft“ und den letzten Fehler. */
export function useAction() {
  const tr = useTr();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (action: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        return true;
      } catch (e) {
        setError(errorText(tr, e));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [tr],
  );
  return { run, busy, error, clear: () => setError(null) };
}

export function ErrorLine({ message }: { message: string | null }) {
  return message === null ? null : (
    <p role="alert" className="mt-2 text-sm text-red-300">
      {message}
    </p>
  );
}

export function Labeled(props: { label: string; children: (id: string) => ReactNode; hint?: string }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm text-slate-300">
        {props.label}
      </label>
      {props.children(id)}
      {props.hint && <p className="mt-1 text-xs text-slate-500">{props.hint}</p>}
    </div>
  );
}

export function TextInput(props: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  maxLength?: number;
  hint?: string;
  multiline?: boolean;
}) {
  return (
    <Labeled label={props.label} hint={props.hint}>
      {(id) =>
        props.multiline ? (
          <textarea
            id={id}
            rows={3}
            className={INPUT}
            maxLength={props.maxLength}
            value={props.value}
            onChange={(e) => props.onChange(e.target.value)}
          />
        ) : (
          <input
            id={id}
            type="text"
            className={INPUT}
            maxLength={props.maxLength}
            value={props.value}
            onChange={(e) => props.onChange(e.target.value)}
          />
        )
      }
    </Labeled>
  );
}

export function SelectInput(props: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  choices: { value: string; label: string }[];
  placeholder?: string;
}) {
  return (
    <Labeled label={props.label}>
      {(id) => (
        <select id={id} className={INPUT} value={props.value} onChange={(e) => props.onChange(e.target.value)}>
          {props.placeholder !== undefined && <option value="">{props.placeholder}</option>}
          {props.choices.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      )}
    </Labeled>
  );
}

export function textChannels(snapshot: Snapshot): { value: string; label: string }[] {
  const text = new Set(snapshot.channels.filter((c) => c.type === "text" || c.type === "news").map((c) => c.id));
  return options(snapshot, "channel")
    .filter((o) => text.has(o.id))
    .map((o) => ({ value: o.id, label: o.label }));
}

export function categoryChannels(snapshot: Snapshot): { value: string; label: string }[] {
  const categories = new Set(snapshot.channels.filter((c) => c.type === "category").map((c) => c.id));
  return options(snapshot, "channel")
    .filter((o) => categories.has(o.id))
    .map((o) => ({ value: o.id, label: o.label.replace(/^▾ /, "") }));
}

export function roleName(snapshot: Snapshot, id: string, tr: Translator): string {
  return snapshot.roles.find((r) => r.id === id)?.name ?? `${tr.t("form.unknown")} (${id})`;
}

export function channelName(snapshot: Snapshot, id: string, tr: Translator): string {
  const channel = snapshot.channels.find((c) => c.id === id);
  return channel === undefined ? `${tr.t("form.unknown")} (${id})` : `#${channel.name}`;
}

const POLL_MS = 1000;
const MAX_POLLS = 30;

/**
 * Veröffentlichen läuft über den Bot: die API stellt nur den Auftrag zu. Danach fragen wir das
 * Ergebnis ab, bis der Bot „fertig“ oder einen Fehler gemeldet hat.
 */
export function PublishBox(props: {
  publish: (channel: string) => Promise<PublishStatus>;
  status: () => Promise<PublishStatus>;
  published: boolean;
  onDone: () => void;
  pollMs?: number;
}) {
  const tr = useTr();
  const { snapshot } = useGuild();
  const channels = textChannels(snapshot);
  const [channel, setChannel] = useState("");
  const [state, setState] = useState<"idle" | "pending" | "done" | "error" | "silent">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const alive = useRef(true);
  const action = useAction();

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  const start = async () => {
    setState("pending");
    setMessage(null);
    setNotice(null);
    const ok = await action.run(async () => {
      await props.publish(channel);
      for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
        if (!alive.current) return;
        const status = await props.status();
        if (status.state === "done" || status.state === "error") {
          setState(status.state);
          setMessage(status.message);
          setNotice(status.notice);
          if (status.state === "done") props.onDone();
          return;
        }
        await wait(props.pollMs ?? POLL_MS);
      }
      setState("silent");
    });
    if (!ok) setState("idle");
  };

  return (
    <div className="mt-3 flex flex-wrap items-end gap-2">
      <div className="min-w-48 flex-1">
        <SelectInput
          label={tr.t("panels.channel")}
          value={channel}
          onChange={setChannel}
          choices={channels}
          placeholder={tr.t("form.choose")}
        />
      </div>
      <button type="button" className={PRIMARY} disabled={channel === "" || state === "pending"} onClick={() => void start()}>
        {props.published ? tr.t("panels.republish") : tr.t("panels.publish")}
      </button>
      <div role="status" className="w-full text-sm">
        {state === "pending" && <span className="text-slate-400">{tr.t("panels.publishing")}</span>}
        {state === "done" && <span className="text-emerald-400">{tr.t("panels.publishDone")}</span>}
        {state === "error" && <span className="text-red-300">{message}</span>}
        {state === "silent" && <span className="text-amber-300">{tr.t("panels.publishNoAnswer")}</span>}
        {notice !== null && <span className="ml-2 text-amber-300">{notice}</span>}
      </div>
      <ErrorLine message={action.error} />
    </div>
  );
}

export function Badge({ children }: { children: ReactNode }) {
  return <span className="rounded bg-slate-700 px-2 py-0.5 text-xs text-slate-300">{children}</span>;
}

export function RoleChip(props: { label: string; onRemove?: () => void; removeLabel: string }) {
  return (
    <li className="flex items-center gap-1 rounded-full bg-slate-700 py-0.5 pl-3 pr-1 text-sm">
      {props.label}
      {props.onRemove && (
        <button
          type="button"
          onClick={props.onRemove}
          className="rounded-full px-1.5 text-slate-400 hover:text-red-300"
          aria-label={props.removeLabel}
        >
          ✕
        </button>
      )}
    </li>
  );
}
