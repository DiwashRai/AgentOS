import { describe, expect, it } from "vitest";

import { counts } from "../../domain/counts";
import { person, run, ticket } from "../../domain/fixtures";
import { groups } from "../../domain/groups";
import { words } from "../../domain/words";
import {
  atWorkNote,
  compactHref,
  listsAnything,
  MINE_QUEUED,
  myRunning,
  rackColumns,
  rackGroups,
  subLine,
} from "./model";

const me = { id: 1 };
const sam = person(2, "Sam Lee");
const now = (over: Partial<ReturnType<typeof ticket>["now"]> = {}) => ({
  actor: null,
  person: null,
  progress: null,
  run_id: null,
  text: "",
  ...over,
});

describe("subLine", () => {
  it("names the agent and run when there is one", () => {
    const t = ticket({ repo_name: "org/web", now: now({ actor: "Implement", run_id: 42 }) });
    expect(subLine(t, me)).toBe("org/web · Implement · run #42");
  });
  it("falls back to the latest run's id", () => {
    const t = ticket({ repo_name: "org/web", latest_run: run("succeeded", { id: 7 }) });
    expect(subLine(t, me)).toBe("org/web · Run #7");
  });
  it("names someone else's ticket, not mine", () => {
    expect(subLine(ticket({ repo_name: "web", owner: sam }), me)).toBe("web · Sam's ticket");
    expect(subLine(ticket({ repo_name: "web", owner: person(1) }), me)).toBe("web");
    expect(subLine(ticket(), me)).toBe("");
  });
  it("leads with the source when the ticket came from one", () => {
    const jira = { source: "jira", external_id: "PLAT-7", url: "https://x/PLAT-7" } as const;
    const t = ticket({ source: jira, repo_name: "web", now: now({ run_id: 3 }) });
    expect(subLine(t, me)).toBe("Jira PLAT-7 · web · Run #3");
  });
});

it("splits mine from the team in the at-work note", () => {
  expect(atWorkNote({ mine: 2, running: 4, queued: 1 })).toBe("2 yours · 3 across the team");
});

describe("the rack", () => {
  const mine = person(1, "Me");
  const tickets = [
    ticket({ key: "A-1", attention: "running", owner: mine, repo_name: "web" }),
    ticket({ key: "A-2", attention: "queued", owner: mine, repo_name: "web" }),
    ticket({ key: "B-1", attention: "queued", owner: sam, repo_name: "web" }),
    ticket({ key: "B-2", attention: "running", owner: sam, repo_name: "web" }),
    ticket({ key: "B-3", attention: "running", owner: null, repo_name: "api" }),
    ticket({ key: "B-4", attention: "running", owner: sam, repo_name: null }),
    ticket({ key: "C-1", attention: "needs_you", owner: sam, repo_name: "web" }),
  ];
  const g = groups(tickets, me);

  it("puts my queued first, then the team's others by repo, running before queued", () => {
    const rack = rackGroups(tickets, g.atWork, me);
    expect(rack.map((x) => [x.name, x.items.map((t) => t.key)])).toEqual([
      [MINE_QUEUED, ["A-2"]],
      ["api", ["B-3"]],
      ["web", ["B-2", "B-1"]],
      [words.noRepo, ["B-4"]],
    ]);
  });
  it("splits my queued runs: the server's runner first, then one group per client wait", () => {
    const wait = now({ text: "waiting for Me's client" });
    const client = (key: string, client_online: boolean) =>
      ticket({
        key,
        attention: "queued",
        owner: mine,
        now: wait,
        latest_run: run("queued", { target: "client", client_online }),
      });
    const all = [...tickets, client("A-3", false), client("A-4", false)];
    const rack = rackGroups(all, groups(all, me).atWork, me);
    expect(rack.slice(0, 2).map((x) => [x.id, x.online, x.items.map((t) => t.key)])).toEqual([
      ["mine-queued", undefined, ["A-2"]],
      [`mine-client:${wait.text}`, "offline", ["A-3", "A-4"]],
    ]);

    const live = [client("A-5", true)];
    const only = rackGroups(live, groups(live, me).atWork, me);
    expect(only.map((x) => [x.id, x.online])).toEqual([[`mine-client:${wait.text}`, "online"]]);
  });
  it("tiles only my running runs", () => {
    expect(myRunning(g.atWork).map((t) => t.key)).toEqual(["A-1"]);
  });
  it("lays groups into the shortest column, in order", () => {
    const grp = (id: string, n: number) => ({
      id,
      name: id,
      items: Array.from({ length: n }, (_, i) => i),
    });
    const cols = rackColumns([grp("a", 4), grp("b", 1), grp("c", 1), grp("d", 1)], 3);
    expect(cols.map((c) => c.map((x) => x.id))).toEqual([["a"], ["b", "d"], ["c"]]);
    expect(rackColumns([], 3)).toEqual([[], [], []]);
  });
  it("counts agree with the groups", () => {
    const c = counts(tickets, me);
    expect(c.mine).toBe(g.atWork.length);
  });
});

it("links to Compact, keeping the search and dropping empty params", () => {
  expect(compactHref({ f: "done", q: "talk pane" })).toBe("/compact?f=done&q=talk+pane");
  expect(compactHref({ q: "" })).toBe("/compact");
});

it("knows when My queue lists nothing (other people's tickets are not on it)", () => {
  const quiet = [
    ticket({ key: "AOS-1", attention: "inbox" }),
    ticket({ key: "AOS-2", attention: "waiting", owner: sam }),
    ticket({ key: "AOS-5", attention: "blocked", owner: sam }),
    ticket({ key: "AOS-6", attention: "done", owner: sam }),
  ];
  expect(listsAnything(groups(quiet, me), rackGroups(quiet, [], me))).toBe(false);
  const blocked = [ticket({ key: "AOS-3", attention: "blocked" })];
  expect(listsAnything(groups(blocked, me), [])).toBe(true);
  const ready = [ticket({ key: "AOS-7", attention: "inbox", owner: person(1) })];
  expect(listsAnything(groups(ready, me), [])).toBe(true);
  const running = [ticket({ key: "AOS-4", attention: "running", owner: sam })];
  expect(listsAnything(groups(running, me), rackGroups(running, [], me))).toBe(true);
});
