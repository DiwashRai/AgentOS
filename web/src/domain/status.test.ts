import { describe, expect, it } from "vitest";

import { person, run, ticket } from "./fixtures";
import { isMine, statusKind } from "./status";
import { attentionWords, statusWords } from "./words";

describe("statusKind", () => {
  it.each([
    ["needs_you", "you"],
    ["waiting", "wait"],
    ["stuck", "blk"],
    ["up_next", "idle"],
    ["done", "done"],
  ] as const)("maps %s to %s", (attention, kind) => {
    expect(statusKind(ticket({ attention }))).toBe(kind);
  });

  it("splits moving on the latest run's status", () => {
    expect(statusKind(ticket({ attention: "moving", latest_run: run("running") }))).toBe("run");
    expect(statusKind(ticket({ attention: "moving", latest_run: run("queued") }))).toBe("queued");
    expect(statusKind(ticket({ attention: "moving", latest_run: run("dispatched") }))).toBe(
      "queued",
    );
  });

  it("treats moving without a run status as running", () => {
    expect(statusKind(ticket({ attention: "moving" }))).toBe("run");
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
    expect(attentionWords.stuck).toBe("Blocked");
    expect(attentionWords.up_next).toBe("Inbox");
  });
});
