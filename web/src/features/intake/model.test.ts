import { describe, expect, it } from "vitest";

import { parseQuery } from "../../domain/search";
import {
  ageParts,
  ageText,
  capText,
  emptyGroupText,
  footNote,
  groupCount,
  headSummary,
  type IntakeItem,
  type IntakeQuery,
  jqlHref,
  matchesItem,
  nameId,
  ROW_CAP,
  searchItems,
  sharedPrefix,
  toTakeIn,
  typeText,
  visibleRows,
} from "./model";

const NOON = Date.parse("2026-10-11T12:00:00Z");

function item(key: string, over: Partial<IntakeItem> = {}): IntakeItem {
  return {
    key,
    title: `Title of ${key}`,
    type: "task",
    priority: "normal",
    status: "To Do",
    assignee: null,
    updated_at: "2026-10-11T11:00:00Z",
    url: `https://jira.example/browse/${key}`,
    ...over,
  };
}

function query(id: string, items: IntakeItem[], over: Partial<IntakeQuery> = {}): IntakeQuery {
  return { id, name: id, jql: "project = X", items, total: items.length, error: null, ...over };
}

const many = (n: number) => Array.from({ length: n }, (_, i) => item(`X-${String(i + 1)}`));

describe("search", () => {
  it("matches every term against the key or the title, case-insensitively", () => {
    const it1 = item("SCRUM-21", { title: "Show the PR link in the header" });
    expect(matchesItem(it1, parseQuery(""))).toBe(true);
    expect(matchesItem(it1, parseQuery("scrum-21"))).toBe(true);
    expect(matchesItem(it1, parseQuery("pr HEADER"))).toBe(true);
    expect(matchesItem(it1, parseQuery("pr footer"))).toBe(false);
  });

  it("filters a group's rows, and returns the same array without a query", () => {
    const items = [item("A-1", { title: "alpha" }), item("A-2", { title: "beta" })];
    expect(searchItems(items, [])).toBe(items);
    expect(searchItems(items, parseQuery("beta")).map((i) => i.key)).toEqual(["A-2"]);
  });
});

describe("counts", () => {
  it("a group counts its listed rows minus the ones taken in", () => {
    expect(groupCount(many(3), new Set())).toBe(3);
    expect(groupCount(many(3), new Set(["X-2"]))).toBe(2);
  });

  it("the top bar counts distinct candidates across queries, minus the taken ones", () => {
    const qs = [
      query("a", [item("A-1"), item("A-2")]),
      query("b", [item("A-2"), item("B-1")], { total: 40 }),
      query("c", [], { error: "Jira down" }),
    ];
    expect(toTakeIn(qs, new Set())).toBe(3);
    expect(toTakeIn(qs, new Set(["A-2"]))).toBe(2);
    expect(toTakeIn([], new Set())).toBe(0);
  });
});

describe("visibleRows", () => {
  it("shows everything up to the cap", () => {
    const items = many(ROW_CAP);
    expect(visibleRows(items, false)).toEqual({ shown: items, rest: 0 });
  });

  it("caps at 8 with the rest behind Show all, and shows all once expanded", () => {
    const items = many(12);
    const capped = visibleRows(items, false);
    expect(capped.shown).toHaveLength(8);
    expect(capped.rest).toBe(4);
    expect(visibleRows(items, true)).toEqual({ shown: items, rest: 0 });
  });
});

describe("cap text", () => {
  it("says how many match in Jira only when the server listed fewer", () => {
    expect(capText(query("a", many(50), { total: 73 }))).toBe("73 match in Jira, newest 50 listed");
    expect(capText(query("a", many(5)))).toBeNull();
    expect(headSummary(query("a", many(50), { total: 73 }))).toBe("50 of 73 match");
    expect(headSummary(query("a", many(5)))).toBeNull();
    expect(headSummary(query("a", [], { total: 3, error: "x" }))).toBeNull();
  });
});

describe("display", () => {
  it("links the JQL into Jira's issue search, or nowhere without a Jira URL", () => {
    expect(jqlHref("https://x.atlassian.net/", "project = A AND labels = b")).toBe(
      "https://x.atlassian.net/issues/?jql=project%20%3D%20A%20AND%20labels%20%3D%20b",
    );
    expect(jqlHref(null, "project = A")).toBeNull();
  });

  it("shows the priority only when it is high", () => {
    expect(typeText(item("A-1", { type: "bug", priority: "high" }))).toEqual({
      type: "Bug",
      priority: "High",
    });
    expect(typeText(item("A-1", { type: "feature", priority: "low" }))).toEqual({
      type: "Feature",
      priority: null,
    });
  });

  it("ages from the Jira update time", () => {
    expect(ageText(item("A-1", { updated_at: "2026-10-11T11:55:00Z" }), NOON)).toBe("5m");
    expect(ageText(item("A-1", { updated_at: "2026-10-11T09:50:00Z" }), NOON)).toBe("2h 10m");
    expect(ageText(item("A-1", { updated_at: "2026-10-09T12:00:00Z" }), NOON)).toBe("2d");
  });

  it("gives an assignee a stable, non-negative avatar id", () => {
    expect(nameId("Maya Chen")).toBe(nameId("Maya Chen"));
    expect(nameId("Maya Chen")).toBeGreaterThanOrEqual(0);
  });

  it("writes the footer note, with the query count apart (it is set in bold)", () => {
    const qs = [query("a", []), query("b", [], { error: "bad JQL" })];
    expect(footNote(qs, NOON - 30_000, NOON)).toEqual({
      count: 2,
      text: "queries · 1 didn't answer · checked just now",
    });
    expect(footNote([query("a", [])], NOON - 120_000, NOON)).toEqual({
      count: 1,
      text: "query · checked 2m ago",
    });
  });

  it("says why a group is empty", () => {
    const quiet = { lead: "All quiet", note: "nothing new matches this query" };
    expect(emptyGroupText(query("a", []), false)).toEqual(quiet);
    expect(emptyGroupText(query("a", []), true)).toEqual(quiet);
    expect(emptyGroupText(query("a", many(2)), true)).toEqual({
      lead: "No match",
      note: "nothing here matches the search",
    });
  });

  it("splits an age into tabular numbers and smaller units", () => {
    expect(ageParts("12m")).toEqual([{ n: "12", unit: "m" }]);
    expect(ageParts("3h 10m")).toEqual([
      { n: "3", unit: "h" },
      { n: "10", unit: "m" },
    ]);
    expect(ageParts("now")).toEqual([{ n: "now", unit: "" }]);
  });
});

describe("sharedPrefix", () => {
  it("is the one project prefix every item shares", () => {
    expect(sharedPrefix([item("SCRUM-24"), item("SCRUM-3"), item("scrum-9")])).toBe("SCRUM");
  });

  it("is null for a mixed group or an empty one", () => {
    expect(sharedPrefix([item("SCRUM-24"), item("AOS-3")])).toBeNull();
    expect(sharedPrefix([])).toBeNull();
  });
});
