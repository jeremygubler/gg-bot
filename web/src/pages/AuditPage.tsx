import { useState } from "react";
import { ApiError, type AuditEntry } from "../api";
import { changedFields, preview } from "../audit";
import { useApi, useGuild } from "../context";
import { useTr } from "../i18n";
import { ErrorBox, Spinner } from "../ui";
import { useAsync } from "../useAsync";

const PAGE = 25; // gleich dem `limit` in api.audit

export function AuditPage() {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const [first, reload] = useAsync(() => api.audit(id), [id]);
  const [older, setOlder] = useState<AuditEntry[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (first.status === "loading") return <Spinner />;
  if (first.status === "error") return <ErrorBox code={first.code} onRetry={reload} />;

  const entries = [...first.data, ...older];
  const last = entries[entries.length - 1];
  const canLoadMore = !exhausted && first.data.length >= PAGE;

  const loadMore = async () => {
    if (last === undefined) return;
    try {
      const page = await api.audit(id, last.id);
      setOlder((current) => [...current, ...page]);
      if (page.length < PAGE) setExhausted(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.code : "unknown");
    }
  };

  return (
    <section>
      <h1 className="mb-4 text-2xl font-bold">{tr.t("audit.title")}</h1>
      {entries.length === 0 && <p className="text-slate-400">{tr.t("audit.empty")}</p>}
      <ul className="space-y-2">
        {entries.map((entry) => {
          const changes = changedFields(entry.before, entry.after);
          return (
            <li key={entry.id} className="rounded border border-slate-700 bg-slate-800/50 p-3">
              <details>
                <summary className="cursor-pointer select-none">
                  <span className="font-medium">{tr.module(entry.module)}</span>
                  <span className="ml-2 text-sm text-slate-400">
                    {new Date(entry.at).toLocaleString(tr.locale)} · {tr.t("audit.by", { user: entry.user_id })}
                  </span>
                  <span className="ml-2 text-sm text-slate-500">
                    {entry.before === null ? tr.t("audit.created") : tr.t("audit.changed", { n: changes.length })}
                  </span>
                </summary>
                <dl className="mt-2 space-y-1 text-sm">
                  {changes.map((change) => (
                    <div key={change.key} className="grid grid-cols-[10rem_1fr] gap-2">
                      <dt className="truncate text-slate-400">{tr.field(change.key)}</dt>
                      <dd className="min-w-0 break-words">
                        <span className="text-red-300 line-through">{preview(change.before)}</span>{" "}
                        <span className="text-emerald-300">{preview(change.after)}</span>
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            </li>
          );
        })}
      </ul>
      {error !== null && <ErrorBox code={error} />}
      {canLoadMore && (
        <button type="button" onClick={() => void loadMore()} className="mt-4 rounded bg-slate-700 px-4 py-2 hover:bg-slate-600">
          {tr.t("audit.more")}
        </button>
      )}
    </section>
  );
}
