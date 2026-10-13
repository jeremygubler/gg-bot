import { createContext, useCallback, useContext, useEffect, useState, type MouseEvent, type ReactNode } from "react";

export type PanelKind = "roles" | "tickets";

export type Route =
  | { name: "home" }
  | { name: "guild"; guild: string }
  | { name: "module"; guild: string; module: string }
  | { name: "audit"; guild: string }
  | { name: "announcements"; guild: string }
  | { name: "panels"; guild: string; kind: PanelKind }
  | { name: "public"; guild: string }
  | { name: "notfound" };

/** Pfad → Route. Kanonische Pfade: `/`, `/g/<id>`, `/g/<id>/m/<modul>`, `/g/<id>/audit`. */
export function parseRoute(pathname: string): Route {
  const parts = pathname.split("/").filter((part) => part !== "");
  if (parts.length === 0) return { name: "home" };
  const isId = (value: string | undefined): value is string => value !== undefined && /^[0-9]{1,20}$/.test(value);
  // Öffentliche Rangliste: ohne Anmeldung erreichbar, deshalb ein eigener Pfad ausserhalb von /g.
  if (parts[0] === "leaderboard" && parts.length === 2 && isId(parts[1])) {
    return { name: "public", guild: parts[1] };
  }
  if (parts[0] !== "g" || !isId(parts[1])) return { name: "notfound" };
  const guild = parts[1];
  if (parts.length === 2) return { name: "guild", guild };
  if (parts.length === 3 && parts[2] === "audit") return { name: "audit", guild };
  if (parts.length === 3 && parts[2] === "announcements") return { name: "announcements", guild };
  if (parts.length === 4 && parts[2] === "panels" && (parts[3] === "roles" || parts[3] === "tickets")) {
    return { name: "panels", guild, kind: parts[3] };
  }
  if (parts.length === 4 && parts[2] === "m" && /^[a-z_]{1,64}$/.test(parts[3]!)) {
    return { name: "module", guild, module: parts[3]! };
  }
  return { name: "notfound" };
}

export const paths = {
  home: () => "/",
  guild: (guild: string) => `/g/${guild}`,
  module: (guild: string, module: string) => `/g/${guild}/m/${module}`,
  audit: (guild: string) => `/g/${guild}/audit`,
  announcements: (guild: string) => `/g/${guild}/announcements`,
  panels: (guild: string, kind: PanelKind) => `/g/${guild}/panels/${kind}`,
  publicLeaderboard: (guild: string) => `/leaderboard/${guild}`,
};

type Guard = () => boolean;

type RouterValue = {
  route: Route;
  navigate: (path: string) => void;
  /** Meldet einen Wächter an (ungespeicherte Änderungen); gibt die Abmeldefunktion zurück. */
  guard: (check: Guard) => () => void;
};

const Context = createContext<RouterValue | null>(null);

export function Router({ children }: { children: ReactNode }) {
  const [pathname, setPathname] = useState(() => window.location.pathname);
  const [guards] = useState(() => new Set<Guard>());

  const allowed = useCallback(() => [...guards].every((check) => check()), [guards]);

  useEffect(() => {
    const onPop = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback(
    (path: string) => {
      if (path === window.location.pathname || !allowed()) return;
      window.history.pushState(null, "", path);
      setPathname(path);
    },
    [allowed],
  );

  const guard = useCallback(
    (check: Guard) => {
      guards.add(check);
      return () => {
        guards.delete(check);
      };
    },
    [guards],
  );

  return <Context.Provider value={{ route: parseRoute(pathname), navigate, guard }}>{children}</Context.Provider>;
}

function useRouter(): RouterValue {
  const value = useContext(Context);
  if (value === null) throw new Error("Router fehlt");
  return value;
}

export const useRoute = (): Route => useRouter().route;
export const useNavigate = (): ((path: string) => void) => useRouter().navigate;

/** Wächter, solange `active`: fragt vor Navigation und vor dem Schliessen des Tabs nach. */
export function useLeaveGuard(active: boolean, message: string): void {
  const { guard } = useRouter();
  useEffect(() => {
    if (!active) return undefined;
    const release = guard(() => window.confirm(message));
    const beforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      release();
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [active, guard, message]);
}

export function Link(props: { to: string; className?: string; children: ReactNode; current?: boolean }) {
  const navigate = useNavigate();
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    // Strg/Cmd/Mittelklick sollen wie gewohnt einen neuen Tab öffnen.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(props.to);
  };
  return (
    <a href={props.to} onClick={onClick} className={props.className} aria-current={props.current ? "page" : undefined}>
      {props.children}
    </a>
  );
}
