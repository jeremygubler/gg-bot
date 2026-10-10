import { useEffect, useMemo, useState } from "react";
import { ApiError, createApi, type Api, type Me } from "./api";
import { ApiContext, MeContext } from "./context";
import { useSetLocale, useTr } from "./i18n";
import { AuditPage } from "./pages/AuditPage";
import { GuildLayout } from "./pages/GuildLayout";
import { ModulePage } from "./pages/ModulePage";
import { ServersPage } from "./pages/Servers";
import { Link, paths, useRoute } from "./router";
import { ErrorBox, Spinner } from "./ui";

type Session = { status: "loading" } | { status: "anonymous"; code: string } | { status: "error"; code: string } | { status: "ready"; me: Me };

export function App({ api: injected }: { api?: Api }) {
  const tr = useTr();
  const setLocale = useSetLocale();
  const [session, setSession] = useState<Session>({ status: "loading" });
  const api = useMemo(
    () => injected ?? createApi(undefined, () => setSession({ status: "anonymous", code: "unauthenticated" })),
    [injected],
  );

  const load = () => {
    setSession({ status: "loading" });
    api.me().then(
      (me) => setSession({ status: "ready", me }),
      (error: unknown) => {
        const code = error instanceof ApiError ? error.code : "unknown";
        // 401 und „Login nicht eingerichtet“ sind keine Störung, sondern der Startzustand.
        setSession({ status: code === "unauthenticated" || code === "dashboard_disabled" ? "anonymous" : "error", code });
      },
    );
  };
  useEffect(load, [api]);

  const logout = () => {
    api.logout().then(
      () => setSession({ status: "anonymous", code: "unauthenticated" }),
      () => setSession({ status: "error", code: "unknown" }),
    );
  };

  return (
    <ApiContext.Provider value={api}>
      <div className="min-h-screen bg-slate-900 text-slate-100">
        <header className="flex items-center gap-3 border-b border-slate-700 px-4 py-3">
          <Link to={paths.home()} className="font-bold">
            GG-Bot
          </Link>
          <span className="flex-1" />
          <button
            type="button"
            onClick={() => setLocale(tr.locale === "de" ? "en" : "de")}
            className="rounded border border-slate-600 px-2 py-1 text-sm hover:bg-slate-700"
            aria-label={tr.locale === "de" ? "Switch to English" : "Auf Deutsch wechseln"}
          >
            {tr.locale === "de" ? "EN" : "DE"}
          </button>
          {session.status === "ready" && (
            <>
              <span className="text-sm text-slate-300">{session.me.username}</span>
              <button type="button" onClick={logout} className="rounded border border-slate-600 px-3 py-1 text-sm hover:bg-slate-700">
                {tr.t("logout")}
              </button>
            </>
          )}
        </header>
        <main>
          {session.status === "loading" && <Spinner />}
          {session.status === "error" && <ErrorBox code={session.code} onRetry={load} />}
          {session.status === "anonymous" && <Login code={session.code} />}
          {session.status === "ready" && (
            <MeContext.Provider value={session.me}>
              <Routes />
            </MeContext.Provider>
          )}
        </main>
      </div>
    </ApiContext.Provider>
  );
}

function Login({ code }: { code: string }) {
  const tr = useTr();
  const disabled = code === "dashboard_disabled";
  return (
    <section className="mx-auto max-w-md p-8 text-center">
      <h1 className="text-3xl font-bold">{tr.t("login.title")}</h1>
      <p className="mt-3 text-slate-400">{tr.t("login.text")}</p>
      {disabled ? (
        <p role="alert" className="mt-6 text-red-300">
          {tr.error("dashboard_disabled")}
        </p>
      ) : (
        // Volle Navigation, kein fetch: der Browser folgt der Weiterleitung zu Discord.
        <a href="/api/auth/login" className="mt-6 inline-block rounded bg-indigo-600 px-5 py-2.5 font-medium hover:bg-indigo-500">
          {tr.t("login.button")}
        </a>
      )}
    </section>
  );
}

function Routes() {
  const route = useRoute();
  const tr = useTr();
  switch (route.name) {
    case "home":
      return <ServersPage />;
    case "guild":
      return (
        <GuildLayout guild={route.guild}>
          <p className="p-2 text-slate-400">{tr.t("guild.choose")}</p>
        </GuildLayout>
      );
    case "module":
      return (
        <GuildLayout guild={route.guild}>
          <ModulePage module={route.module} />
        </GuildLayout>
      );
    case "audit":
      return (
        <GuildLayout guild={route.guild}>
          <AuditPage />
        </GuildLayout>
      );
    case "notfound":
      return <ErrorBox code="module_not_found" />;
  }
}
