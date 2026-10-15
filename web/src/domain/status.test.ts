import { describe, expect, it } from "vitest";

import { person, run, ticket } from "./fixtures";
import { isMine, statusKind } from "./status";
import { attentionWords, statusWords } from "./words";

describe("statusKind", () => {
  it.each([
    ["needs_you", "you"],
    ["waiting", "wait"],
    ["running", "run"],
    ["queued", "queued"],
    ["blocked", "blk"],
    ["inbox", "idle"],
    ["done", "done"],
  ] as const)("maps %s to %s", (attention, kind) => {
    expect(statusKind(ticket({ attention }))).toBe(kind);
  });

  it("reads only attention, never the run status", () => {
    expect(statusKind(ticket({ attention: "queued", latest_run: run("running") }))).toBe("queued");
    expect(statusKind(ticket({ attention: "running", latest_run: run("dispatched") }))).toBe("run");
  });
});

describe("isMine", () => {
  it("compares the owner's id with mine", () => {
    expect(isMine(ticket({ owner: person(7) }), { id: 7 })).toBe(true);
    expect(isMine(ticket({ owner: person(8) }), { id: 7 })).toBe(false);
    expect(isMine(ticket({ owner: null }), { id: 7 })).toBe(false);
    expect(isMine(ticket({ owner: person(7) }), null)).toBe(false);
  });
});

describe("words", () => {
  it("never says Stuck or Needs me", () => {
    const all = [...Object.values(statusWords), ...Object.values(attentionWords)];
    expect(all.join(" ")).not.toMatch(/stuck|needs me/i);
    expect(attentionWords.blocked).toBe("Blocked");
    expect(attentionWords.inbox).toBe("Inbox");
  });
});
