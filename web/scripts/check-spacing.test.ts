import { describe, expect, it } from "vitest";

import { check, findViolations, isSpacingProperty, rawLengths } from "./check-spacing";

describe("isSpacingProperty", () => {
  it.each([
    ["margin", true],
    ["padding-left", true],
    ["margin-inline", true],
    ["padding-block-start", true],
    ["gap", true],
    ["row-gap", true],
    ["column-gap", true],
    ["Margin-Top", true],
    ["width", false],
    ["border-width", false],
    ["scroll-margin", false],
    ["--gap", false],
    ["padding-middle", false],
  ])("%s → %s", (property, expected) => {
    expect(isSpacingProperty(property)).toBe(expected);
  });
});

describe("rawLengths", () => {
  it.each([
    ["8px", ["8px"]],
    ["0 12px", ["12px"]],
    ["-6px", ["-6px"]],
    ["2.5px 0", ["2.5px"]],
    ["1rem .5em", ["1rem", ".5em"]],
    ["0", []],
    ["0px", []],
    ["auto", []],
    ["50%", []],
    ["var(--sp-10) var(--sp-4)", []],
    ["calc(-1 * var(--sp-4))", []],
    ["calc(var(--sp-11) + var(--sp-8))", []],
    ["calc(12px - var(--sp-zone))", ["12px"]],
    ["var(--x, 4px)", ["4px"]],
    ["min(var(--sp-1), calc(40% / var(--n, 1)))", []],
  ])("%s → %j", (value, expected) => {
    expect(rawLengths(value)).toEqual(expected);
  });
});

describe("findViolations", () => {
  it("reports raw spacing with its line, and skips comments, selectors and other properties", () => {
    const css = [
      "/* padding: 4px; */",
      ".a:hover {",
      "  width: 12px;",
      "  padding: 3px",
      "    var(--sp-2);",
      "  gap: var(--sp-4);",
      "}",
      ".b { margin-top: 5px }",
    ].join("\n");

    expect(findViolations("x.module.css", css)).toEqual([
      { file: "x.module.css", line: 4, declaration: "padding: 3px var(--sp-2)" },
      { file: "x.module.css", line: 8, declaration: "margin-top: 5px" },
    ]);
  });
});

describe("check", () => {
  it("passes allowlisted declarations and reports entries that match nothing", () => {
    const allowed = [
      { file: "a.module.css", declaration: "margin: -1px", reason: "hidden" },
      { file: "a.module.css", declaration: "margin: 1px", reason: "stale" },
    ];

    const result = check(
      [
        { file: "a.module.css", css: ".x { margin: -1px; }" },
        { file: "b.module.css", css: ".x { margin: -1px; }" },
      ],
      allowed,
    );

    expect(result.violations.map((v) => v.file)).toEqual(["b.module.css"]);
    expect(result.unused).toEqual([allowed[1]]);
  });
});
