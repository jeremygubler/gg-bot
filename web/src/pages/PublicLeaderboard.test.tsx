import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { PublicRow } from "../api";
import { backendRef, mount, setupBackend } from "../test/mount";

setupBackend();

function rows(count: number, from = 1): PublicRow[] {
  return Array.from({ length: count }, (_, i) => ({
    position: from + i,
    name: i === 1 ? null : `Mitglied${from + i}`,
    avatar_url: i === 1 ? null : `https://cdn.example/${from + i}.png`,
    level: 5,
    xp: 12345,
  }));
}

describe("öffentliche Rangliste", () => {
  it("braucht keine Anmeldung und fragt /me nicht ab", async () => {
    backendRef.current.loggedIn = false;
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 1, rows: rows(3) };
    mount("/leaderboard/100");

    expect(await screen.findByRole("heading", { name: "Rangliste von Alpha" })).toBeInTheDocument();

    expect(backendRef.current.calls.map((c) => c.path)).toEqual(["/api/public/100/leaderboard?page=1"]);
    expect(screen.queryByRole("link", { name: "Mit Discord anmelden" })).not.toBeInTheDocument();
  });

  it("zeigt Platz, Name, Level und XP; unbekannte Namen als Platzhalter", async () => {
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 1, rows: rows(3) };
    mount("/leaderboard/100");

    const list = await screen.findByRole("list");
    const items = within(list).getAllByRole("listitem");

    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("#1");
    expect(items[0]).toHaveTextContent("Mitglied1");
    expect(items[0]).toHaveTextContent("Level 5");
    expect(items[0]).toHaveTextContent("12.345 XP");
    expect(items[1]).toHaveTextContent("Mitglied"); // Platzhalter statt Name
    expect(items[1]!.querySelector("img")).toBeNull();
  });

  it("Avatare werden ohne Referrer geladen", async () => {
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 1, rows: rows(1) };
    mount("/leaderboard/100");

    const list = await screen.findByRole("list");

    expect(list.querySelector("img")).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("blättert", async () => {
    const user = userEvent.setup();
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 2, rows: rows(2) };
    mount("/leaderboard/100");
    await screen.findByText("Seite 1 von 2");
    expect(screen.getByRole("button", { name: "Zurück" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Weiter" }));

    await screen.findByText("Seite 2 von 2");
    expect(backendRef.current.calls.at(-1)!.path).toBe("/api/public/100/leaderboard?page=2");
    expect(screen.getByRole("button", { name: "Weiter" })).toBeDisabled();
  });

  it("keine Blätterleiste bei einer Seite", async () => {
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 1, rows: rows(2) };
    mount("/leaderboard/100");
    await screen.findByRole("list");

    expect(screen.queryByRole("button", { name: "Weiter" })).not.toBeInTheDocument();
  });

  it.each([
    [404, "not_found", "nicht freigegeben"],
    [503, "board_unavailable", "wird gerade erstellt"],
    [429, "rate_limited", "Zu viele"],
  ])("Fehler %i zeigt eine verständliche Meldung", async (status, error, text) => {
    backendRef.current.board = { status, error };
    mount("/leaderboard/100");

    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("leere Liste", async () => {
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 1, rows: [] };
    mount("/leaderboard/100");

    expect(await screen.findByText("Noch keine Einträge.")).toBeInTheDocument();
  });

  it("Sprache umschalten", async () => {
    const user = userEvent.setup();
    backendRef.current.board = { guild_name: "Alpha", page: 1, pages: 1, rows: rows(1) };
    mount("/leaderboard/100");
    await screen.findByText("Rangliste von Alpha");

    await user.click(screen.getByRole("button", { name: "EN" }));

    expect(screen.getByText("Leaderboard of Alpha")).toBeInTheDocument();
    localStorage.removeItem("ggbot.locale");
  });
});
