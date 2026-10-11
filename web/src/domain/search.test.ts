import { describe, expect, it } from "vitest";

import { ticket } from "./fixtures";
import { bySearch, jumpTarget, matchesQuery, parseQuery } from "./search";

const polish = ticket({ key: "AOS-1", title: "Board polish" });
const talk = ticket({ key: "AOS-2", title: "Talk pane: show the thread" });
const jira = ticket({
  key: "AOS-3",
  title: "Import the backlog",
  source: { source: "jira", external_id: "PLAT-1", url: "" },
});
const search = ticket({ key: "AOS-12", title: "Board search" });
const board = [polish, talk, jira, search];

describe("parseQuery", () => {
  it("lowercases and splits on any whitespace", () => {
    expect(parseQuery("  Board\tSEARCH  ")).toEqual(["board", "search"]);
  });

  it("is empty for nothing", () => {
    expect(parseQuery("")).toEqual([]);
    expect(parseQuery("   ")).toEqual([]);
    expect(parseQuery(null)).toEqual([]);
  });
});

describe("matchesQuery", () => {
  it("matches everything with no terms", () => {
    expect(matchesQuery(polish, [])).toBe(true);
  });

  it("matches the key, the title and the source label, ignoring case", () => {
    expect(matchesQuery(talk, parseQuery("aos-2"))).toBe(true);
    expect(matchesQuery(talk, parseQuery("TALK"))).toBe(true);
    expect(matchesQuery(jira, parseQuery("jira plat-1"))).toBe(true);
    expect(matchesQuery(jira, parseQuery("plat"))).toBe(true);
  });

  it("needs every term to match", () => {
    expect(matchesQuery(search, parseQuery("board search"))).toBe(true);
    expect(matchesQuery(polish, parseQuery("board search"))).toBe(false);
  });

  it("does not match a term across fields", () => {
    expect(matchesQuery(polish, parseQuery("aos-1board"))).toBe(false);
  });
});

describe("bySearch", () => {
  it("returns the same array with no query", () => {
    expect(bySearch(board, [])).toBe(board);
  });

  it("keeps the matches in order", () => {
    expect(bySearch(board, parseQuery("board")).map((t) => t.key)).toEqual(["AOS-1", "AOS-12"]);
  });
});

describe("jumpTarget", () => {
  it("goes straight to a key-shaped query, uppercased, even off the board", () => {
    expect(jumpTarget("aos-4", board)).toBe("AOS-4");
    expect(jumpTarget(" plat-2231 ", board)).toBe("PLAT-2231");
  });

  it("opens the one ticket that matches", () => {
    expect(jumpTarget("talk", board)).toBe("AOS-2");
    expect(jumpTarget("jira", board)).toBe("AOS-3");
  });

  it("goes nowhere with several matches, none, or no query", () => {
    expect(jumpTarget("board", board)).toBeNull();
    expect(jumpTarget("zzz", board)).toBeNull();
    expect(jumpTarget("  ", board)).toBeNull();
  });
});
