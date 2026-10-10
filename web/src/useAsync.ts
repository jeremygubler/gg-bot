import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

export type Async<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; code: string };

/** Lädt beim Einhängen und bei Änderung von `deps`; `reload` erzwingt ein erneutes Laden. */
export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]): [Async<T>, () => void] {
  const [state, setState] = useState<Async<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let current = true;
    setState({ status: "loading" });
    load().then(
      (data) => current && setState({ status: "ready", data }),
      (error: unknown) => current && setState({ status: "error", code: error instanceof ApiError ? error.code : "unknown" }),
    );
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` ist absichtlich nicht Teil der Abhängigkeiten
  }, [...deps, nonce]);

  return [state, useCallback(() => setNonce((n) => n + 1), [])];
}
