import type { ReactNode } from "react";
import { useTr } from "./i18n";

export function Spinner() {
  const tr = useTr();
  return (
    <p role="status" className="p-6 text-slate-400">
      {tr.t("loading")}
    </p>
  );
}

export function ErrorBox(props: { code: string; onRetry?: () => void; children?: ReactNode }) {
  const tr = useTr();
  return (
    <div role="alert" className="m-6 rounded border border-red-500/50 bg-red-950/40 p-4 text-red-200">
      <p>{tr.error(props.code)}</p>
      {props.children}
      {props.onRetry && (
        <button type="button" onClick={props.onRetry} className="mt-3 rounded bg-slate-700 px-3 py-1 text-slate-100 hover:bg-slate-600">
          {tr.t("retry")}
        </button>
      )}
    </div>
  );
}
