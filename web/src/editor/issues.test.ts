import { describe, expect, it } from "vitest";

import { ApiError, type ValidationIssue } from "../api/client";
import {
  countWithin,
  errorAt,
  issueCount,
  issuesOf,
  messagesAt,
  pathOf,
  placeIssues,
  type Issues,
  type PlaceOptions,
} from "./issues";

function issue(loc: (string | number)[], message = "bad"): ValidationIssue {
  return { loc, message };
}

/** The placed issues as plain data, for `toEqual`. */
function plain(issues: Issues) {
  return { fields: Object.fromEntries(issues.fields), form: issues.form };
}

const repos = { keys: { repos: ["k1", "k2"] } };
const nested = { keys: { mappings: ["m1", "m2"], "mappings.m2.rules": ["r1", "r2"] } };

describe("placeIssues", () => {
  const cases: [string, ValidationIssue[], PlaceOptions, ReturnType<typeof plain>][] = [
    [
      "drops the value root",
      [issue(["value", "space_name"])],
      {},
      { fields: { space_name: ["bad"] }, form: [] },
    ],
    ["an empty loc is form level", [issue([])], {}, { fields: {}, form: ["bad"] }],
    ["the document itself is form level", [issue(["value"])], {}, { fields: {}, form: ["bad"] }],
    [
      "a loc outside the root is form level, with its loc",
      [issue(["base_version"])],
      {},
      { fields: {}, form: ["base_version: bad"] },
    ],
    [
      "a list index becomes the row key sent at that index",
      [issue(["value", "repos", 1, "url"])],
      repos,
      { fields: { "repos.k2.url": ["bad"] }, form: [] },
    ],
    [
      "an index outside the snapshot is form level",
      [issue(["value", "repos", 2, "url"])],
      repos,
      { fields: {}, form: ["repos.2.url: bad"] },
    ],
    [
      "a list-level issue sits on the list's path",
      [issue(["value", "repos"])],
      repos,
      { fields: { repos: ["bad"] }, form: [] },
    ],
    [
      "nested lists map through the parent row's key",
      [issue(["value", "mappings", 1, "rules", 0, "match"])],
      nested,
      { fields: { "mappings.m2.rules.r1.match": ["bad"] }, form: [] },
    ],
    [
      "a list without keys keeps its index",
      [issue(["value", "jira_projects", 2])],
      {},
      { fields: { "jira_projects.2": ["bad"] }, form: [] },
    ],
    [
      "another root, e.g. a preview body",
      [issue(["mapping", "rules", 1, "value"])],
      { root: ["mapping"], keys: { rules: ["r1", "r2"] } },
      { fields: { "rules.r2.value": ["bad"] }, form: [] },
    ],
    [
      "an empty root keeps the whole loc",
      [issue(["role"])],
      { root: [] },
      { fields: { role: ["bad"] }, form: [] },
    ],
    [
      "with shown paths, an issue lands on the deepest shown one",
      [issue(["value", "jira_projects", 2]), issue(["value", "repos", 0, "url"])],
      { ...repos, shown: ["jira_projects", "repos.*.url"] },
      { fields: { jira_projects: ["bad"], "repos.k1.url": ["bad"] }, form: [] },
    ],
    [
      "with shown paths, an issue under none of them is form level",
      [issue(["value", "repos", 0, "branch"])],
      { ...repos, shown: ["repos.*.url"] },
      { fields: {}, form: ["repos.0.branch: bad"] },
    ],
    [
      "several issues on one field keep their order",
      [issue(["value", "name"], "a"), issue(["value", "name"], "b")],
      {},
      { fields: { name: ["a", "b"] }, form: [] },
    ],
  ];

  it.each(cases)("%s", (_, issues, options, expected) => {
    expect(plain(placeIssues(issues, options))).toEqual(expected);
  });

  it("an issue stays on its row after the rows move", () => {
    const placed = placeIssues([issue(["value", "repos", 0, "url"])], repos);
    const movedRows = ["k2", "k1"];

    const flagged = movedRows.filter((key) => errorAt(placed, pathOf("repos", key, "url")));

    expect(flagged).toEqual(["k1"]);
  });
});

describe("reading placed issues", () => {
  const placed = placeIssues(
    [
      issue(["value", "repos"], "list"),
      issue(["value", "repos", 0, "url"], "a"),
      issue(["value", "repos", 0, "url"], "b"),
      issue(["value", "repos", 1, "name"], "c"),
      issue([], "form"),
    ],
    repos,
  );

  it.each([
    ["the list-level messages", messagesAt(placed, "repos"), ["list"]],
    ["nothing at a clean field", messagesAt(placed, "repos.k2.url"), []],
    ["a field's messages as one error", errorAt(placed, "repos.k1.url"), "a b"],
    ["no error at a clean field", errorAt(placed, "repos.k2.url"), undefined],
    ["a row's count", countWithin(placed, "repos.k1"), 2],
    ["a list's count includes its own", countWithin(placed, "repos"), 4],
    ["the total, form level included", issueCount(placed), 5],
  ])("%s", (_, actual, expected) => {
    expect(actual).toEqual(expected);
  });
});

describe("issuesOf", () => {
  it.each([
    ["a 422's issues", new ApiError(422, "validation_error", "x", [issue(["value", "a"])]), 1],
    ["an error without issues", new ApiError(409, "stale_version", "x"), 0],
    ["not an ApiError", new Error("x"), 0],
  ])("%s", (_, error, count) => {
    expect(issueCount(issuesOf(error))).toBe(count);
  });
});
