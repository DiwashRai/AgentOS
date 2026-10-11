import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import { planSections, planText, sectionCount, showsSections, type PlanLike } from "./plan";

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
