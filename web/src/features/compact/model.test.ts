import { describe, expect, it } from "vitest";

import { gate, person, run, step, ticket } from "../../domain/fixtures";
import {
  byFilter,
  collapsedByDefault,
  compactGroups,
  emptyText,
  footNote,
  GROUP_ORDER,
  inRepo,
  moveText,
  nowCell,
  ownerLine,
  parseFilter,
  pills,
  runLine,
  sourceText,
  talkLabel,
  updText,
  withFilter,
} from "./model";

const me = { id: 1 };
const mine = person(1, "Me Myself");
const sam = person(2, "Sam Lee");
const at = (hh: string) => `2026-10-11T${hh}:00:00Z`;
const NOON = Date.parse(at("12"));
const keys = (ts: { key: string }[]) => ts.map((t) => t.key);
const now = (over: Partial<ReturnType<typeof ticket>["now"]> = {}) => ({
  actor: null,
  person: null,
  progress: null,
  run_id: null,
  text: "",
  ...over,
});

describe("filters", () => {
  it("parses ?f= to an attention value, anything else is everything", () => {
    for (const a of GROUP_ORDER) expect(parseFilter(a)).toBe(a);
    expect(parseFilter(null)).toBe("all");
    expect(parseFilter("needYou")).toBe("all");
  });
  it("sets or drops f and keeps the other params", () => {
    const p = new URLSearchParams("f=blocked&peek=AOS-1&repo=web");
    expect(withFilter(p, "inbox").toString()).toBe("f=inbox&peek=AOS-1&repo=web");
    expect(withFilter(p, "all").toString()).toBe("peek=AOS-1&repo=web");
    expect(p.get("f")).toBe("blocked");
  });
  it("filters by exact repo name and by attention", () => {
    const ts = [
      ticket({ key: "A", repo_name: "org/web", attention: "inbox" }),
      ticket({ key: "B", repo_name: "org/api", attention: "running" }),
      ticket({ key: "C", repo_name: null, attention: "inbox" }),
    ];
    expect(keys([...inRepo(ts, "org/web")])).toEqual(["A"]);
    expect(inRepo(ts, null)).toBe(ts);
    expect(keys([...byFilter(ts, "inbox")])).toEqual(["A", "C"]);
    expect(byFilter(ts, "all")).toBe(ts);
  });
});

describe("pills", () => {
  const ts = [
    ticket({ key: "Y1", attention: "needs_you" }),
    ticket({ key: "Y2", attention: "needs_you" }),
    ticket({ key: "R", attention: "running" }),
    ticket({ key: "I", attention: "inbox" }),
    ticket({ key: "D", attention: "done" }),
  ];
  it("counts each pill from the shared counts, equal to its group", () => {
    const p = pills(ts, me, "all");
    const groups = compactGroups(ts, me, NOON);
    for (const pill of p) {
      if (pill.filter === "all") continue;
      const g = groups.find((x) => x.id === pill.filter);
      expect(pill.count).toBe(g?.items.length ?? 0);
    }
    expect(p[0]).toEqual({ filter: "all", label: "Everything", mark: null, count: 5 });
    expect(p.find((x) => x.filter === "inbox")).toMatchObject({ label: "Inbox", mark: "idle" });
  });
  it("shows the Done pill only while Done is the filter", () => {
    expect(pills(ts, me, "all").some((x) => x.filter === "done")).toBe(false);
    expect(pills(ts, me, "done").find((x) => x.filter === "done")?.count).toBe(1);
  });
});

describe("groups", () => {
  it("orders groups by the design and drops empty ones", () => {
    const ts = [
      ticket({ key: "D", attention: "done" }),
      ticket({ key: "I", attention: "inbox" }),
      ticket({ key: "Y", attention: "needs_you" }),
      ticket({ key: "B", attention: "blocked" }),
    ];
    expect(compactGroups(ts, me, NOON).map((g) => [g.id, g.title, g.mark])).toEqual([
      ["needs_you", "Needs you", "you"],
      ["blocked", "Blocked", "blk"],
      ["inbox", "Inbox", "idle"],
      ["done", "Done recently", "done"],
    ]);
  });
  it("sorts waits longest first, done newest first, the rest by repo (none last)", () => {
    const ts = [
      ticket({ key: "Y1", attention: "needs_you", attention_since: at("10") }),
      ticket({ key: "Y2", attention: "needs_you", attention_since: at("08") }),
      ticket({ key: "D1", attention: "done", attention_since: at("08") }),
      ticket({ key: "D2", attention: "done", attention_since: at("11") }),
      ticket({ key: "I1", attention: "inbox", repo_name: null, attention_since: at("08") }),
      ticket({ key: "I2", attention: "inbox", repo_name: "web", attention_since: at("10") }),
      ticket({ key: "I3", attention: "inbox", repo_name: "api", attention_since: at("11") }),
      ticket({ key: "I4", attention: "inbox", repo_name: "web", attention_since: at("09") }),
    ];
    const g = Object.fromEntries(compactGroups(ts, me, NOON).map((x) => [x.id, keys(x.items)]));
    expect(g).toEqual({
      needs_you: ["Y2", "Y1"],
      inbox: ["I3", "I4", "I2", "I1"],
      done: ["D2", "D1"],
    });
  });
  it("summarises: longest wait, blocked yours, repos for 4+", () => {
    const ts = [
      ticket({ key: "Y1", attention: "needs_you", attention_since: at("09") }),
      ticket({ key: "Y2", attention: "needs_you", attention_since: at("11") }),
      ticket({ key: "B1", attention: "blocked", owner: mine }),
      ticket({ key: "B2", attention: "blocked", owner: sam }),
      ticket({ key: "Q1", attention: "queued", repo_name: "org/web" }),
      ticket({ key: "R1", attention: "running", repo_name: "org/web" }),
      ticket({ key: "R2", attention: "running", repo_name: "org/web" }),
      ticket({ key: "R3", attention: "running", repo_name: "org/api" }),
      ticket({ key: "R4", attention: "running", repo_name: null }),
    ];
    const g = Object.fromEntries(compactGroups(ts, me, NOON).map((x) => [x.id, x.summary]));
    expect(g.needs_you).toEqual(["longest wait ", { b: "3h" }]);
    expect(g.blocked).toEqual([{ b: "1" }, " yours"]);
    expect(g.running).toEqual([
      "web ",
      { b: "2" },
      " · api ",
      { b: "1" },
      " · No repo ",
      { b: "1" },
    ]);
    expect(g.queued).toEqual([]);
  });
  it("collapses Done by default unless it is the filter", () => {
    expect(collapsedByDefault("done", "all")).toBe(true);
    expect(collapsedByDefault("done", "done")).toBe(false);
    expect(collapsedByDefault("inbox", "all")).toBe(false);
  });
});

describe("texts", () => {
  it("counts tickets and repos in the footer", () => {
    expect(footNote([ticket({ repo_name: "a" }), ticket({ repo_name: "a" }), ticket()])).toBe(
      "3 tickets · 1 repo",
    );
    expect(footNote([])).toBe("0 tickets · 0 repos");
  });
  it("says what is empty, in the repo when scoped", () => {
    expect(emptyText("needs_you", null)).toBe("Nothing needs you");
    expect(emptyText("inbox", "org/web")).toBe("Nothing in the inbox in web");
  });
  it("labels the source column and the upd column", () => {
    const src = { source: "jira" as const, external_id: "PLAT-1", url: "" };
    expect(sourceText(ticket({ source: src, repo_name: "org/web" }))).toBe("JIRA · web");
    expect(sourceText(ticket({ repo_name: "org/web" }))).toBe("web");
    expect(sourceText(ticket())).toBe("");
    expect(updText(ticket({ attention: "running", attention_since: at("09") }), NOON)).toBe("now");
    expect(updText(ticket({ attention: "inbox", attention_since: at("11") }), NOON)).toBe("1h");
  });
  it("talks in messages", () => {
    expect(talkLabel(1)).toBe("Talk · 1 message");
    expect(talkLabel(0)).toBe("Talk · 0 messages");
  });
  it("names the owner and the stages", () => {
    expect(ownerLine(ticket({ owner: sam }), "5 stages")).toBe("Owner Sam Lee · 5 stages");
    expect(ownerLine(ticket(), "1 stage")).toBe("Owner nobody · 1 stage");
  });
});

describe("the now column", () => {
  it("shows the live run inline while running", () => {
    const t = ticket({
      attention: "running",
      now: now({ actor: "Build", text: "Running tests", progress: { done: 2, total: 5 } }),
    });
    expect(nowCell(t)).toEqual({
      kind: "run",
      actor: "Build",
      text: "Running tests",
      meter: { k: 3, n: 5, state: "running" },
    });
  });
  it("shows the YOU form with the parked gate, and the wait on a person", () => {
    const you = ticket({
      attention: "needs_you",
      rail: [step("Plan", "done", { gate: gate("you", { label: "Plan sign-off" }) })],
      now: now({ text: "3 tasks" }),
    });
    expect(nowCell(you)).toEqual({
      kind: "mark",
      mark: { kind: "you", ask: "Plan sign-off" },
      text: "3 tasks",
    });
    const wait = ticket({
      attention: "waiting",
      rail: [
        step("Review", "done", { gate: gate("waiting", { label: "PR review", person: sam }) }),
      ],
    });
    expect(nowCell(wait)).toMatchObject({ mark: { kind: "wait", name: "Sam", ask: "PR review" } });
  });
  it("gives blocked its reason and the inbox its note", () => {
    const blk = ticket({
      attention: "blocked",
      now: now({ text: "Build failed" }),
      latest_run: run("failed", { error: "exit 1" }),
    });
    expect(nowCell(blk)).toMatchObject({ mark: { kind: "blk" }, text: "Build failed · exit 1" });
    expect(nowCell(ticket({ attention: "inbox" }))).toMatchObject({ text: "Nobody on it yet" });
    expect(nowCell(ticket({ attention: "inbox", now: now({ text: "Draft" }) })).text).toBe("Draft");
  });
  it("marks done tickets by outcome, with the note", () => {
    const abandoned = ticket({
      attention: "done",
      state: "closed",
      resolution: { outcome: "abandoned", note: "Superseded", by: sam, at: at("10") },
    });
    expect(nowCell(abandoned)).toEqual({
      kind: "mark",
      mark: { kind: "abandoned", name: "Abandoned" },
      text: "Superseded",
    });
    expect(nowCell(ticket({ attention: "done", state: "done" }))).toMatchObject({
      mark: { kind: "done", name: "Complete" },
    });
  });
});

describe("the expansion", () => {
  it("leads with the gate on you, the person when waiting, else the status", () => {
    const you = ticket({
      attention: "needs_you",
      rail: [step("Plan", "done", { gate: gate("you", { label: "Plan sign-off" }) })],
      now: now({ text: "3 tasks" }),
    });
    expect(moveText(you)).toEqual({ label: "Your move", lead: "Plan sign-off: 3 tasks" });
    expect(moveText(ticket({ attention: "needs_you", now: now({ text: "Run passed" }) }))).toEqual({
      label: "Your move",
      lead: "Run passed",
    });
    const wait = ticket({ attention: "waiting", now: now({ person: sam, text: "Review" }) });
    expect(moveText(wait)).toEqual({ label: "Waiting on", lead: "Sam · Review" });
    expect(moveText(ticket({ attention: "queued" }))).toEqual({ label: "Now", lead: "Queued" });
  });
  it("labels a done ticket with its outcome, over the note", () => {
    const abandoned = ticket({
      attention: "done",
      state: "closed",
      resolution: { outcome: "abandoned", note: "Superseded", by: sam, at: at("10") },
    });
    expect(moveText(abandoned)).toEqual({ label: "Abandoned", lead: "Superseded" });
    expect(moveText(ticket({ attention: "done", state: "done" }))).toEqual({
      label: "Complete",
      lead: "Complete",
    });
  });
  it("labels the run with the parts the server sent", () => {
    const t = ticket({
      attention: "running",
      now: now({ actor: "Build", run_id: 42, progress: { done: 0, total: 3 } }),
      latest_run: run("running", { id: 42, started_at: at("11") }),
    });
    expect(runLine(t, Date.parse("2026-10-11T11:03:12Z"))).toBe("Build · Run #42 · 3:12 · 1/3");
    expect(runLine(ticket({ attention: "queued" }), NOON)).toBe("Run");
  });
});
