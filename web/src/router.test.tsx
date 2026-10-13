import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Link, Router, parseRoute, paths, useLeaveGuard, useRoute } from "./router";

describe("parseRoute", () => {
  it("kennt die kanonischen Pfade", () => {
    expect(parseRoute("/")).toEqual({ name: "home" });
    expect(parseRoute("/g/123")).toEqual({ name: "guild", guild: "123" });
    expect(parseRoute("/g/123/audit")).toEqual({ name: "audit", guild: "123" });
    expect(parseRoute("/g/123/announcements")).toEqual({ name: "announcements", guild: "123" });
    expect(parseRoute("/g/123/m/starboard")).toEqual({ name: "module", guild: "123", module: "starboard" });
    expect(parseRoute("/g/123/m/starboard/")).toEqual({ name: "module", guild: "123", module: "starboard" });
    expect(parseRoute("/g/123/panels/roles")).toEqual({ name: "panels", guild: "123", kind: "roles" });
    expect(parseRoute("/g/123/panels/tickets")).toEqual({ name: "panels", guild: "123", kind: "tickets" });
    expect(parseRoute("/leaderboard/123")).toEqual({ name: "public", guild: "123" });
  });

  it("weist alles andere ab, auch gefährlich Aussehendes", () => {
    for (const bad of ["/x", "/g", "/g/abc", "/g/1/m", "/g/1/m/a/b", "/g/1/m/../x", "/g/1/m/A", "/g/" + "1".repeat(21), "/g/1/zzz", "/g/1/panels", "/g/1/panels/x", "/g/1/panels/roles/x", "/leaderboard", "/leaderboard/abc", "/leaderboard/1/2"]) {
      expect(parseRoute(bad), bad).toEqual({ name: "notfound" });
    }
  });

  it("paths und parseRoute sind zueinander passend", () => {
    expect(parseRoute(paths.module("5", "logging"))).toEqual({ name: "module", guild: "5", module: "logging" });
    expect(parseRoute(paths.audit("5"))).toEqual({ name: "audit", guild: "5" });
    expect(parseRoute(paths.announcements("5"))).toEqual({ name: "announcements", guild: "5" });
    expect(parseRoute(paths.guild("5"))).toEqual({ name: "guild", guild: "5" });
    expect(parseRoute(paths.home())).toEqual({ name: "home" });
    expect(parseRoute(paths.panels("5", "tickets"))).toEqual({ name: "panels", guild: "5", kind: "tickets" });
    expect(parseRoute(paths.publicLeaderboard("5"))).toEqual({ name: "public", guild: "5" });
  });
});

function Where() {
  return <p data-testid="where">{JSON.stringify(useRoute())}</p>;
}

function Guarded({ dirty }: { dirty: boolean }) {
  useLeaveGuard(dirty, "ungespeichert");
  return null;
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

describe("Router", () => {
  it("navigiert per Link ohne Neuladen und aktualisiert die Adresse", async () => {
    const user = userEvent.setup();
    render(
      <Router>
        <Link to="/g/9">weiter</Link>
        <Where />
      </Router>,
    );

    await user.click(screen.getByText("weiter"));

    expect(window.location.pathname).toBe("/g/9");
    expect(screen.getByTestId("where")).toHaveTextContent('"guild":"9"');
  });

  it("Strg+Klick bleibt dem Browser überlassen", async () => {
    const user = userEvent.setup();
    render(
      <Router>
        <Link to="/g/9">weiter</Link>
      </Router>,
    );

    // jsdom kann keine echte Navigation; der Browser würde hier den Link selbst öffnen.
    screen.getByText("weiter").addEventListener("click", (event) => event.preventDefault());
    await user.keyboard("{Control>}");
    await user.click(screen.getByText("weiter"));
    await user.keyboard("{/Control}");

    expect(window.location.pathname).toBe("/");
  });

  it("reagiert auf Zurück/Vor", () => {
    render(
      <Router>
        <Where />
      </Router>,
    );
    act(() => {
      window.history.pushState(null, "", "/g/4/audit");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("where")).toHaveTextContent('"audit"');
  });

  it("fragt bei ungespeicherten Änderungen nach und bleibt bei „Abbrechen“", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <Router>
        <Guarded dirty />
        <Link to="/g/9">weiter</Link>
      </Router>,
    );

    await user.click(screen.getByText("weiter"));
    expect(confirm).toHaveBeenCalledWith("ungespeichert");
    expect(window.location.pathname).toBe("/");

    confirm.mockReturnValue(true);
    await user.click(screen.getByText("weiter"));
    expect(window.location.pathname).toBe("/g/9");
  });

  it("ohne Änderungen wird nicht gefragt", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm");
    render(
      <Router>
        <Guarded dirty={false} />
        <Link to="/g/9">weiter</Link>
      </Router>,
    );

    await user.click(screen.getByText("weiter"));

    expect(confirm).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/g/9");
  });
});
