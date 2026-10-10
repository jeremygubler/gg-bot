import { useMemo, useState } from "react";
import { ApiError } from "../api";
import { useApi, useGuild } from "../context";
import { SchemaForm } from "../form/SchemaForm";
import { useTr } from "../i18n";
import { useLeaveGuard } from "../router";
import { compile, equal, indexErrors, SchemaError, type FieldError, type JsonObject } from "../schema";
import { ErrorBox, Spinner } from "../ui";
import { useAsync } from "../useAsync";

export function ModulePage({ module }: { module: string }) {
  const api = useApi();
  const { id } = useGuild();
  // Beim Wechsel des Moduls oder Servers neu aufbauen, sonst bliebe der Formularzustand des alten stehen.
  const [loaded, reload] = useAsync(
    async () => {
      const [schema, settings] = await Promise.all([api.schema(module), api.settings(id, module)]);
      return { schema, settings };
    },
    [id, module],
  );

  if (loaded.status === "loading") return <Spinner />;
  if (loaded.status === "error") return <ErrorBox code={loaded.code} onRetry={reload} />;
  return <ModuleForm key={`${id}/${module}`} module={module} schema={loaded.data.schema} initial={loaded.data.settings} />;
}

type Notice = { kind: "saved" } | { kind: "error"; code: string } | null;

export function ModuleForm(props: { module: string; schema: object; initial: JsonObject }) {
  const api = useApi();
  const tr = useTr();
  const { id, snapshot } = useGuild();
  const [saved, setSaved] = useState(props.initial);
  const [value, setValue] = useState(props.initial);
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);
  const [notice, setNotice] = useState<Notice>(null);

  const compiled = useMemo(() => {
    try {
      return compile(props.schema);
    } catch (error) {
      if (error instanceof SchemaError) return null;
      throw error;
    }
  }, [props.schema]);

  const dirty = !equal(value, saved);
  useLeaveGuard(dirty, tr.t("form.confirmLeave"));
  const errors = useMemo(() => indexErrors(fieldErrors), [fieldErrors]);

  if (compiled === null) return <ErrorBox code="schema" />;

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const result = await api.save(id, props.module, value);
      setSaved(result);
      setValue(result);
      setFieldErrors([]);
      setNotice({ kind: "saved" });
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(error.fields);
        setNotice({ kind: "error", code: error.code });
      } else {
        setNotice({ kind: "error", code: "unknown" });
      }
    } finally {
      setSaving(false);
    }
  };

  const general = errors.get("") ?? [];

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
      aria-label={tr.module(props.module)}
    >
      <h1 className="mb-4 text-2xl font-bold">{tr.module(props.module)}</h1>
      {general.length > 0 && (
        <ul role="alert" className="mb-4 rounded border border-red-500/50 bg-red-950/40 p-3 text-red-200">
          {general.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
      <SchemaForm
        spec={compiled}
        value={value}
        onChange={(next) => {
          setValue(next);
          setNotice(null);
        }}
        snapshot={snapshot}
        errors={errors}
      />
      <div className="sticky bottom-0 -mx-1 mt-6 flex flex-wrap items-center gap-3 border-t border-slate-700 bg-slate-900/95 px-1 py-3">
        <button
          type="submit"
          disabled={!dirty || saving}
          className="rounded bg-indigo-600 px-4 py-2 font-medium hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? tr.t("form.saving") : tr.t("form.save")}
        </button>
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => {
            setValue(saved);
            setFieldErrors([]);
            setNotice(null);
          }}
          className="rounded border border-slate-600 px-4 py-2 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {tr.t("form.reset")}
        </button>
        {dirty && <span className="text-sm text-amber-300">{tr.t("form.unsaved")}</span>}
        <span role="status" className="text-sm">
          {notice?.kind === "saved" && <span className="text-emerald-400">{tr.t("form.saved")}</span>}
          {notice?.kind === "error" && (
            <span className="text-red-300">
              {notice.code === "invalid_settings" ? tr.t("form.invalid") : tr.error(notice.code)}
            </span>
          )}
        </span>
      </div>
    </form>
  );
}
