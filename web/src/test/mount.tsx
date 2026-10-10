import { render } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { App } from "../App";
import { LocaleProvider, type Locale } from "../i18n";
import { Router } from "../router";
import { FakeBackend } from "./fakeBackend";

export const backendRef: { current: FakeBackend } = { current: new FakeBackend() };

export function mount(path = "/", locale: Locale = "de") {
  window.history.replaceState(null, "", path);
  return render(
    <LocaleProvider initial={locale}>
      <Router>
        <App />
      </Router>
    </LocaleProvider>,
  );
}

/** Fake-Backend vor jedem Test neu aufsetzen und `fetch` darauf umleiten. */
export function setupBackend(): void {
  beforeEach(() => {
    backendRef.current = new FakeBackend();
    vi.stubGlobal("fetch", backendRef.current.fetch);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState(null, "", "/");
  });
}
