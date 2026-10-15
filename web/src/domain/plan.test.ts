import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import {
  firstParagraph,
  paragraphs,
  plainText,
  planSections,
  planText,
  planView,
  sectionCount,
  showsSections,
  type PlanLike,
} from "./plan";

describe("planText", () => {
  it.each([
    ["1. Baseline check", "Baseline check"],
    ["12. Wire the route", "Wire the route"],
    ["3) Add tests", "Add tests"],
    ["  4.\tIndented", "Indented"],
  ])("strips the leading number from %j", (raw, text) => {
    expect(planText(raw)).toBe(text);
  });
  it.each(["1.2 release notes", "2026 roadmap", "v1. migrate", "Step 1. later", "1.", "No number"])(
    "keeps %j",
    (raw) => {
      expect(planText(raw)).toBe(raw);
    },
  );
});

describe("planSections", () => {
  const item = (text: string, section?: string | null, done = false): PlanLike =>
    section === undefined ? { text, done } : { text, done, section };
  const shape = (items: PlanLike[]) =>
    planSections(items).map((s) => ({
      section: s.section,
      idx: s.items.map((e) => e.index),
      count: sectionCount(s),
    }));

  it("is one null section when items carry no section field (today's contract)", () => {
    const plan: Schemas["PlanItem"][] = [
      { text: "a", done: true },
      { text: "b", done: false },
    ];
    const sections = planSections(plan);
    expect(shape(plan)).toEqual([{ section: null, idx: [0, 1], count: "1 of 2" }]);
    expect(showsSections(sections)).toBe(false);
  });

  it("is one null section when every section is null", () => {
    const plan = [item("a", null), item("b", null)];
    expect(shape(plan)).toEqual([{ section: null, idx: [0, 1], count: "0 of 2" }]);
    expect(showsSections(planSections(plan))).toBe(false);
  });

  it("shows no headers for a single section", () => {
    const plan = [item("a", "Build", true), item("b", "Build")];
    expect(shape(plan)).toEqual([{ section: "Build", idx: [0, 1], count: "1 of 2" }]);
    expect(showsSections(planSections(plan))).toBe(false);
  });

  it("groups consecutive items and keeps each item's overall index", () => {
    const plan = [
      item("intro", null),
      item("a", "Plan", true),
      item("b", "Plan", true),
      item("c", "Build", true),
      item("d", "Build"),
      item("e", "Verify"),
    ];
    const sections = planSections(plan);
    expect(shape(plan)).toEqual([
      { section: null, idx: [0], count: "0 of 1" },
      { section: "Plan", idx: [1, 2], count: "2 of 2" },
      { section: "Build", idx: [3, 4], count: "1 of 2" },
      { section: "Verify", idx: [5], count: "0 of 1" },
    ]);
    expect(sections[2]?.items.map((e) => e.item.text)).toEqual(["c", "d"]);
    expect(showsSections(sections)).toBe(true);
  });

  it("starts a new section when a heading recurs non-consecutively", () => {
    const plan = [item("a", "Build"), item("b", "Notes"), item("c", "Build")];
    expect(shape(plan).map((s) => [s.section, s.idx])).toEqual([
      ["Build", [0]],
      ["Notes", [1]],
      ["Build", [2]],
    ]);
  });

  it("is empty for an empty plan", () => {
    expect(planSections([])).toEqual([]);
  });
});

describe("planView", () => {
  const plan = (n: number, done = 0) =>
    Array.from({ length: n }, (_, i) => ({ text: `step ${String(i)}`, done: i < done }));

  it("caps at max with +N more and counts done over all items", () => {
    const v = planView(plan(12, 10), 8);
    expect([v?.items.length, v?.more, v?.done, v?.total, v?.pct]).toEqual([8, 4, 10, 12, 83]);
  });
  it("shows the item rather than '+1 more', and everything without a max", () => {
    expect([planView(plan(9), 8)?.items.length, planView(plan(9), 8)?.more]).toEqual([9, 0]);
    expect(planView(plan(30))?.items).toHaveLength(30);
  });
  it("counts no plan and an empty plan as none", () => {
    expect(planView([])).toBeNull();
    expect(planView(null)).toBeNull();
  });
  it("gives each item plain text and keeps the raw text", () => {
    const item = planView([{ text: "1. Add a `--shout` flag", done: true }])?.items[0];
    expect([item?.text, item?.raw, item?.done]).toEqual([
      "Add a --shout flag",
      "Add a `--shout` flag",
      true,
    ]);
  });
  it("heads sections only with two or more", () => {
    const two = planView([
      { text: "a", done: true, section: "Plan" },
      { text: "b", done: false, section: "Build" },
    ]);
    expect([two?.headed, two?.sections.length]).toEqual([true, 2]);
    expect(planView(plan(3))?.headed).toBe(false);
  });
});

describe("description text", () => {
  it("strips markdown to plain text", () => {
    expect(plainText("Add a `--shout` flag to **greet.py** ([docs](http://x))")).toBe(
      "Add a --shout flag to greet.py (docs)",
    );
    expect(plainText("## Heading\n- item *one*")).toBe("Heading item one");
  });
  it("splits paragraphs on blank lines and takes the first as the plain lead", () => {
    const text = "First *line*\nstill first.\r\n\r\n  Second.  \n\n\n";
    expect(paragraphs(text)).toEqual(["First *line*\nstill first.", "Second."]);
    expect(firstParagraph(text)).toEqual({
      text: "First line still first.",
      raw: "First *line*\nstill first.",
      rest: ["Second."],
    });
    expect(firstParagraph("   ").text).toBe("");
  });
  it("skips a paragraph with no plain text for the lead and keeps it in the body", () => {
    const text = "![](shot.png)\n\nThe lead.\n\nMore.";
    expect(firstParagraph(text)).toEqual({
      text: "The lead.",
      raw: "The lead.",
      rest: ["![](shot.png)", "More."],
    });
  });
});
