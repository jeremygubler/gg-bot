import { useEffect, useState } from "react";
import { ApiError, createApi, type PublicLeaderboard as Board } from "../api";
import { useSetLocale, useTr } from "../i18n";

type State = { status: "loading" } | { status: "ready"; board: Board } | { status: "error"; code: string };

const api = createApi();

/** Öffentliche Seite: ohne Anmeldung, nur lesen. Zeigt, was der Server freigegeben hat. */
export function PublicLeaderboardPage({ guild }: { guild: string }) {
  const tr = useTr();
  const setLocale = useSetLocale();
  const [page, setPage] = useState(1);
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let current = true;
    setState({ status: "loading" });
    api.publicLeaderboard(guild, page).then(
      (board) => current && setState({ status: "ready", board }),
      (error: unknown) => current && setState({ status: "error", code: error instanceof ApiError ? error.code : "unknown" }),
    );
    return () => {
      current = false;
    };
  }, [guild, page]);

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <header className="flex items-center border-b border-slate-700 px-4 py-3">
        <span className="flex-1 font-bold">GG-Bot</span>
        <button
          type="button"
          onClick={() => setLocale(tr.locale === "de" ? "en" : "de")}
          className="rounded border border-slate-600 px-2 py-1 text-sm hover:bg-slate-700"
        >
          {tr.locale === "de" ? "EN" : "DE"}
        </button>
      </header>
      <main className="mx-auto max-w-2xl p-4">
        {state.status === "loading" && <p role="status" className="text-slate-400">{tr.t("loading")}</p>}
        {state.status === "error" && (
          <p role="alert" className="rounded border border-red-500/50 bg-red-950/40 p-4 text-red-200">
            {tr.error(state.code)}
          </p>
        )}
        {state.status === "ready" && (
          <>
            <h1 className="mb-4 text-2xl font-bold">{tr.t("public.title", { server: state.board.guild_name })}</h1>
            {state.board.rows.length === 0 && <p className="text-slate-400">{tr.t("public.empty")}</p>}
            <ol className="space-y-2">
              {state.board.rows.map((row) => (
                <li key={row.position} className="flex items-center gap-3 rounded border border-slate-700 bg-slate-800/50 p-2">
                  <span className="w-10 text-center font-semibold text-slate-300">#{row.position}</span>
                  {row.avatar_url !== null ? (
                    <img
                      src={row.avatar_url}
                      alt=""
                      width={36}
                      height={36}
                      className="rounded-full"
                      referrerPolicy="no-referrer"
                    />
                  ) : (
                    <span aria-hidden="true" className="h-9 w-9 rounded-full bg-slate-700" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{row.name ?? tr.t("public.unknown")}</span>
                  <span className="text-sm text-slate-400">
                    {tr.t("public.level")} {row.level} · {row.xp.toLocaleString(tr.locale)} {tr.t("public.xp")}
                  </span>
                </li>
              ))}
            </ol>
            {state.board.pages > 1 && (
              <nav className="mt-4 flex items-center gap-3" aria-label="Pagination">
                <button
                  type="button"
                  disabled={state.board.page <= 1}
                  onClick={() => setPage(state.board.page - 1)}
                  className="rounded bg-slate-700 px-3 py-1.5 text-sm hover:bg-slate-600 disabled:opacity-40"
                >
                  {tr.t("public.prev")}
                </button>
                <span className="text-sm text-slate-400">
                  {tr.t("public.page", { page: state.board.page, pages: state.board.pages })}
                </span>
                <button
                  type="button"
                  disabled={state.board.page >= state.board.pages}
                  onClick={() => setPage(state.board.page + 1)}
                  className="rounded bg-slate-700 px-3 py-1.5 text-sm hover:bg-slate-600 disabled:opacity-40"
                >
                  {tr.t("public.next")}
                </button>
              </nav>
            )}
          </>
        )}
      </main>
    </div>
  );
}
