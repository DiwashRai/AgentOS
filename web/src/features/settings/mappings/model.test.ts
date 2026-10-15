import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import {
  addMapping,
  addRule,
  fieldOptions,
  fieldsProblem,
  formOf,
  isBlankMapping,
  issueCount,
  listMissing,
  locateIssue,
  mergeIssues,
  moveRule,
  parseValues,
  patchMapping,
  patchRule,
  previewRequest,
  removeMapping,
  removeRule,
  sameForm,
  serverIssues,
  toBody,
  tryBlock,
  tryErrorText,
  tryLine,
  typedField,
  validate,
  validateMapping,
  type FieldMappings,
  type JiraField,
  type MappingDraft,
} from "./model";

const DOC: FieldMappings = {
  version: 3,
  updated_at: "2026-10-13T08:00:00Z",
  updated_by: null,
  mappings: [
    {
      target: "base_branch",
      source: "jira",
      field: "fixVersions",
      field_name: "Fix versions",
      rules: [
        { match: "version_1*", value: "App-1.Latest" },
        { match: "version_2*", value: "App-2.Latest" },
      ],
    },
  ],
};

function draft(): MappingDraft {
  const d = formOf(DOC)[0];
  if (!d) throw new Error("no draft");
  return d;
}

describe("draft and body", () => {
  it("round-trips the saved document, trimming and dropping the rule keys", () => {
    const form = formOf(DOC);
    expect(form[0]?.rules.map((r) => r.key)).toEqual(["s:0", "s:1"]);
    expect(toBody(form, 3)).toEqual({ mappings: DOC.mappings, base_version: 3 });

    const edited = patchMapping(form, "base_branch", {
      rules: patchRule(draft().rules, 0, { match: "  version_1* ", value: " App-1.Latest" }),
    });
    expect(toBody(edited, 3).mappings[0]?.rules[0]).toEqual({
      match: "version_1*",
      value: "App-1.Latest",
    });
  });

  it("compares content, not keys", () => {
    const a = formOf(DOC);
    const rekeyed = a.map((m) => ({ ...m, rules: m.rules.map((r) => ({ ...r, key: "x" })) }));
    expect(sameForm(a, rekeyed)).toBe(true);
    const moved = patchMapping(a, "base_branch", { rules: moveRule(draft().rules, 0, 1) });
    expect(sameForm(a, moved)).toBe(false);
  });

  it("adds a mapping once, with one empty rule, and removes it", () => {
    const added = addMapping([], "base_branch");
    expect(added).toEqual([
      {
        target: "base_branch",
        field: "",
        field_name: "",
        rules: [{ key: "n:1", match: "", value: "" }],
      },
    ]);
    expect(addMapping(added, "base_branch")).toBe(added);
    expect(added[0] && isBlankMapping(added[0])).toBe(true);
    expect(isBlankMapping(draft())).toBe(false);
    expect(removeMapping(formOf(DOC), "base_branch")).toEqual([]);
  });

  it("adds, moves and removes rules", () => {
    let rules = addRule(draft().rules);
    rules = addRule(rules);
    expect(rules.map((r) => r.key)).toEqual(["s:0", "s:1", "n:1", "n:2"]);
    expect(moveRule(rules, 0, -1)).toBe(rules);
    expect(moveRule(rules, 3, 1)).toBe(rules);
    expect(moveRule(rules, 1, -1).map((r) => r.key)).toEqual(["s:1", "s:0", "n:1", "n:2"]);
    expect(removeRule(rules, 2).map((r) => r.key)).toEqual(["s:0", "s:1", "n:2"]);
  });
});

describe("validate", () => {
  it("passes the saved document", () => {
    expect(issueCount(validate(formOf(DOC)))).toBe(0);
  });

  it("flags a missing field, empty or long matches, and bad branch names", () => {
    const d: MappingDraft = {
      target: "base_branch",
      field: "",
      field_name: "",
      rules: [
        { key: "a", match: " ", value: "App-1.Latest" },
        { key: "b", match: "x".repeat(201), value: "bad..branch" },
        { key: "c", match: "v3*", value: "" },
        { key: "d", match: "v4*", value: "release/" },
      ],
    };
    const issues = validateMapping(d);
    expect(issues.field).toBe("Pick a Jira field, or enter its id.");
    expect(issues.rules[0]).toEqual({ match: "Required." });
    expect(issues.rules[1]).toEqual({
      match: "At most 200 characters.",
      value: "Must not contain .. or //.",
    });
    expect(issues.rules[2]).toEqual({ value: "Required." });
    expect(issues.rules[3]?.value).toBe("Must not end in /, . or .lock.");
    expect(issueCount([issues])).toBe(6);
  });

  it("caps the rule count", () => {
    const rules = Array.from({ length: 101 }, (_, i) => ({
      key: String(i),
      match: `v${String(i)}*`,
      value: "main",
    }));
    expect(validateMapping({ ...draft(), rules }).form).toEqual(["At most 100 rules."]);
  });
});

describe("the server's 422", () => {
  it("locates a save's, FastAPI's and a preview's paths", () => {
    expect(locateIssue("mappings.0.rules.3.value: not a valid branch")).toEqual({
      mapping: 0,
      rule: 3,
      part: "value",
      text: "not a valid branch",
    });
    expect(
      locateIssue("body.mappings.1.rules.0.match: String should have at most 200 characters"),
    ).toMatchObject({ mapping: 1, rule: 0, part: "match" });
    expect(locateIssue("mapping.rules.2.value: bad")).toMatchObject({ mapping: null, rule: 2 });
    expect(locateIssue("body.mappings.0.field: String should match pattern")).toMatchObject({
      mapping: 0,
      rule: null,
      part: "field",
    });
    expect(locateIssue("mappings.1.target: 'base_branch' has a mapping already")).toBeNull();
    expect(locateIssue("issue: not a Jira issue key or link to one")).toBeNull();
  });

  it("puts the issue on its rule while that rule still holds what was sent", () => {
    const form = formOf(DOC);
    const sent = toBody(form, 3);
    const error = new ApiError(422, "validation_error", "mappings.0.rules.1.value: no good");
    const found = serverIssues(error, sent, form);
    expect(found?.index).toBe(0);
    expect(found?.issues.rules[1]).toEqual({ value: "No good" });

    const merged = mergeIssues(validateMapping(draft()), found?.issues);
    expect(merged.rules[1]).toEqual({ value: "No good" });

    const edited = patchMapping(form, "base_branch", {
      rules: patchRule(draft().rules, 1, { value: "App-2.Next" }),
    });
    expect(serverIssues(error, sent, edited)).toBeNull();
    // Another code, or no request, names nothing.
    expect(serverIssues(new ApiError(409, "stale_version", "x"), sent, form)).toBeNull();
    expect(serverIssues(error, undefined, form)).toBeNull();
  });

  it("lets the client's own issue win over the server's", () => {
    const client = { rules: [{ value: "Required." }], form: [] };
    const server = { rules: [{ value: "Server says", match: "Too long" }], form: [] };
    expect(mergeIssues(client, server).rules[0]).toEqual({
      value: "Required.",
      match: "Too long",
    });
  });
});

describe("field picker", () => {
  const FIELDS: JiraField[] = [
    { id: "customfield_1", name: "Release", kind: "option" },
    { id: "customfield_2", name: "Release", kind: "string" },
    { id: "fixVersions", name: "Fix versions", kind: "version" },
  ];

  it("lists names, with the id only where a name repeats", () => {
    expect(fieldOptions(FIELDS, { field: "fixVersions", field_name: "Fix versions" })).toEqual([
      { value: "customfield_1", label: "Release (customfield_1)" },
      { value: "customfield_2", label: "Release (customfield_2)" },
      { value: "fixVersions", label: "Fix versions" },
    ]);
  });

  it("keeps the saved field when the list failed or lacks it, and starts empty on Choose", () => {
    expect(fieldOptions(undefined, { field: "fixVersions", field_name: "Fix versions" })).toEqual([
      { value: "fixVersions", label: "Fix versions" },
    ]);
    expect(fieldOptions([], { field: "", field_name: "" })).toEqual([
      { value: "", label: "Choose a Jira field" },
    ]);
  });

  it("takes an id typed by hand, naming it from Jira's list when the list has it", () => {
    expect(typedField(FIELDS, " fixVersions ", "")).toEqual({
      field: " fixVersions ",
      field_name: "Fix versions",
    });
    expect(typedField(undefined, "customfield_10042", "Release")).toEqual({
      field: "customfield_10042",
      field_name: "Release",
    });
    const typed = { ...draft(), field: "customfield_10042", field_name: "" };
    expect(validateMapping(typed).field).toBe("Name the field, e.g. Fix versions.");
    expect(validateMapping({ ...typed, field: "custom field" }).field).toBe(
      "custom field isn't a Jira field id.",
    );
    expect(validateMapping({ ...typed, field_name: "Release" }).field).toBeUndefined();
  });

  it("starts on typing the id once the list has failed, not while it loads", () => {
    expect(listMissing({ pending: true, data: undefined })).toBe(false);
    expect(listMissing({ pending: false, data: undefined })).toBe(true);
    expect(listMissing({ pending: false, data: FIELDS })).toBe(false);
  });

  it("explains a 501 without Retry, and offers Retry otherwise", () => {
    expect(fieldsProblem(new ApiError(501, "not_implemented", "x")).retry).toBe(false);
    const bad = fieldsProblem(new ApiError(502, "upstream_error", "Jira didn't answer"));
    expect(bad).toEqual({ text: "Can't load Jira's fields · Jira didn't answer", retry: true });
  });
});

describe("Try it", () => {
  it("splits comma-separated values and builds the request from the unsaved rules", () => {
    expect(parseValues(" version_1.0, ,version_2.0 ,")).toEqual(["version_1.0", "version_2.0"]);
    const req = previewRequest(draft(), "values", "version_1.0");
    expect(req).toEqual({ mapping: DOC.mappings[0], values: ["version_1.0"] });
    expect(previewRequest(draft(), "issue", "  PLAT-1 ")).toEqual({
      mapping: DOC.mappings[0],
      issue: "PLAT-1",
    });
    expect(previewRequest(draft(), "values", " , ")).toBeUndefined();
  });

  it("is off until the field and rules are fine and something is typed", () => {
    const ok = validateMapping(draft());
    expect(tryBlock(ok, "values", "")).toBe("Type a value");
    expect(tryBlock(ok, "issue", " ")).toBe("Type an issue key or link");
    expect(tryBlock(ok, "values", "v1")).toBeUndefined();
    expect(tryBlock({ ...ok, field: "Pick a Jira field." }, "values", "v1")).toBe(
      "Pick a Jira field first",
    );
    expect(tryBlock({ ...ok, rules: [{ value: "Required." }] }, "values", "v1")).toBe(
      "Fix the rules first",
    );
  });

  it("shows a result only while the rules and the input are the ones that ran", () => {
    const ran = previewRequest(draft(), "values", "version_2.1");
    const data = { values: ["version_2.1"], value: "App-2.Latest", rule: 1, reason: null };
    const state = { ran, pending: false, data, error: null };
    expect(tryLine(state, ran)).toEqual({
      kind: "result",
      read: ["version_2.1"],
      value: "App-2.Latest",
      rule: 1,
      reason: null,
    });
    const changed = previewRequest(
      { ...draft(), rules: removeRule(draft().rules, 0) },
      "values",
      "version_2.1",
    );
    expect(tryLine(state, changed)).toEqual({ kind: "none" });
    expect(tryLine({ ...state, pending: true }, ran)).toEqual({ kind: "pending" });
    expect(tryLine({ ran: undefined, pending: false, data: undefined, error: null }, ran)).toEqual({
      kind: "none",
    });
  });

  it("says why a preview failed", () => {
    expect(tryErrorText(new ApiError(501, "not_implemented", "x"))).toContain("Try a value");
    expect(tryErrorText(new ApiError(404, "ticket_not_found", "Jira can't see PLAT-9"))).toBe(
      "Jira can't see PLAT-9. Check the key, or that the service account can see it.",
    );
    expect(tryErrorText(new ApiError(502, "upstream_error", "Jira is down"))).toBe(
      "Jira didn't answer · Jira is down",
    );
    expect(
      tryErrorText(new ApiError(422, "validation_error", "mapping.rules.0.value: bad name")),
    ).toBe("Rule 1: Bad name");
    expect(tryErrorText(new ApiError(422, "validation_error", "issue: not a Jira issue key"))).toBe(
      "Not a Jira issue key.",
    );
  });
});
