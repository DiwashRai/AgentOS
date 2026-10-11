import { describe, expect, it } from "vitest";

import { firstParagraph, planView, plainText, stagesText, trapIndex } from "./model";
import { withoutParam, withParam } from "./params";

describe("description", () => {
  it("strips markdown to plain text", () => {
    expect(plainText("Add a `--shout` flag to **greet.py** ([docs](http://x))")).toBe(
      "Add a --shout flag to greet.py (docs)",
    );
    expect(plainText("## Heading\n- item *one*")).toBe("Heading item one");
  });
  it("takes the first non-empty paragraph", () => {
    expect(firstParagraph("\n\nFirst line\ncontinues.\n\nSecond.")).toBe("First line continues.");
    expect(firstParagraph("")).toBe("");
  });
});

describe("planView", () => {
  const plan = (n: number, done = 0) =>
    Array.from({ length: n }, (_, i) => ({ text: `step ${String(i)}`, done: i < done }));
  it("caps at 8 with +N more and counts done over all items", () => {
    const v = planView(plan(12, 10));
    expect(v.items).toHaveLength(8);
    expect(v.more).toBe(4);
    expect([v.done, v.total]).toEqual([10, 12]);
  });
  it("shows a 9th item rather than '+1 more'", () => {
    expect(planView(plan(9)).more).toBe(0);
    expect(planView(plan(9)).items).toHaveLength(9);
  });
});

it("counts stages that will run", () => {
  expect(stagesText([{ state: "done" }, { state: "skipped" }, { state: "pending" }])).toBe(
    "3 stages · 1 skipped",
  );
  expect(stagesText([{ state: "done" }])).toBe("1 stage");
});

describe("trapIndex", () => {
  it("enters at the first (Tab) or last (Shift+Tab) from the panel", () => {
    expect(trapIndex(-1, 4, false)).toBe(0);
    expect(trapIndex(-1, 4, true)).toBe(3);
  });
  it("wraps at the ends and leaves the middle to the browser", () => {
    expect(trapIndex(3, 4, false)).toBe(0);
    expect(trapIndex(0, 4, true)).toBe(3);
    expect(trapIndex(1, 4, false)).toBeNull();
    expect(trapIndex(2, 4, true)).toBeNull();
  });
  it("keeps focus on the panel when nothing inside is focusable", () => {
    expect(trapIndex(-1, 0, false)).toBe(-1);
  });
});

it("changes one search param and keeps the rest", () => {
  const p = new URLSearchParams("f=blocked&peek=AOS-1");
  expect(withoutParam(p, "peek").toString()).toBe("f=blocked");
  expect(withParam(p, "peek", "AOS-2").toString()).toBe("f=blocked&peek=AOS-2");
  expect(p.toString()).toBe("f=blocked&peek=AOS-1");
});
