import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import { placeIssues } from "../../../editor/issues";
import {
  fieldOptions,
  fieldsProblem,
  formKeys,
  freeTarget,
  fromForm,
  listMissing,
  newMapping,
  parseValues,
  previewRequest,
  ruleFieldName,
  ruleWord,
  sameForm,
  SHOWN,
  specOf,
  toForm,
  tryErrorLines,
  tryLine,
  typedField,
  type JiraField,
  type MappingForm,
  type MappingsDoc,
} from "./model";

const DOC: MappingsDoc = {
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

function first(form = toForm(DOC)): { key: string; item: MappingForm } {
  const row = form.mappings[0];
  if (!row) throw new Error("no mapping");
  return row;
}

describe("form and value", () => {
  it("round-trips the saved document, trimming and dropping the keys", () => {
    const form = toForm(DOC);
    expect(fromForm(form)).toEqual(DOC);

    const row = first(form);
    const [r0, r1] = row.item.rules;
    if (!r0 || !r1) throw new Error("no rules");
    const edited = {
      mappings: [
        {
          ...row,
          item: {
            ...row.item,
            field: " fixVersions ",
            rules: [{ ...r0, item: { match: "  version_1* ", value: " App-1.Latest" } }, r1],
          },
        },
      ],
    };
    expect(fromForm(edited)).toEqual(DOC);
  });

  it("compares content, not keys", () => {
    const a = toForm(DOC);
    expect(sameForm(a, toForm(DOC))).toBe(true);
    const row = first(a);
    const moved = {
      mappings: [{ ...row, item: { ...row.item, rules: [...row.item.rules].reverse() } }],
    };
    expect(sameForm(a, moved)).toBe(false);
  });

  it("gives the row keys of the mappings and of each one's rules", () => {
    const form = toForm(DOC);
    const row = first(form);
    const keys = formKeys(form);
    expect(keys).toEqual({
      mappings: [row.key],
      [`mappings.${row.key}.rules`]: row.item.rules.map((r) => r.key),
    });

    // A rule's issue lands on that rule's field, by key.
    const [, second] = row.item.rules;
    const placed = placeIssues(
      [{ loc: ["value", "mappings", 0, "rules", 1, "value"], message: "bad" }],
      { keys, shown: SHOWN },
    );
    expect([...placed.fields.keys()]).toEqual([
      `mappings.${row.key}.rules.${second?.key ?? ""}.value`,
    ]);
  });

  it("offers only targets without a mapping, a new one starting with one empty rule", () => {
    expect(freeTarget(toForm(DOC))).toBeUndefined();
    expect(freeTarget({ mappings: [] })).toBe("base_branch");
    expect(fromForm({ mappings: [newMapping("base_branch")] })).toEqual({
      mappings: [
        {
          target: "base_branch",
          source: "jira",
          field: "",
          field_name: "",
          rules: [{ match: "", value: "" }],
        },
      ],
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
  });

  it("starts on typing the id once the list has failed, not while it loads", () => {
    expect(listMissing({ pending: true, data: undefined })).toBe(false);
    expect(listMissing({ pending: false, data: undefined })).toBe(true);
    expect(listMissing({ pending: false, data: FIELDS })).toBe(false);
  });

  it("explains a 501 without Retry, and offers Retry otherwise", () => {
    expect(fieldsProblem(new ApiError(501, "not_implemented", "x")).retry).toBe(false);
    expect(fieldsProblem(new ApiError(502, "upstream_error", "down")).retry).toBe(true);
  });
});

describe("Try it", () => {
  const spec = specOf("base_branch");

  it("splits comma-separated values and builds the request from the unsaved rules", () => {
    const mapping = first().item;
    expect(parseValues(" version_1.0, ,version_2.0 ,")).toEqual(["version_1.0", "version_2.0"]);
    expect(previewRequest(mapping, "values", "version_1.0")).toEqual({
      mapping: DOC.mappings[0],
      issue: null,
      values: ["version_1.0"],
    });
    expect(previewRequest(mapping, "issue", "  PLAT-1 ")).toEqual({
      mapping: DOC.mappings[0],
      issue: "PLAT-1",
      values: null,
    });
    expect(previewRequest(mapping, "values", " , ")).toBeUndefined();
    expect(previewRequest(mapping, "issue", " ")).toBeUndefined();
  });

  it("shows pending, then the result or the failure", () => {
    const keys = ["a", "b"];
    expect(tryLine({ pending: true, result: undefined, error: undefined }, keys, spec).kind).toBe(
      "pending",
    );
    expect(tryLine({ pending: false, result: undefined, error: undefined }, keys, spec).kind).toBe(
      "none",
    );
    const result = { values: ["version_2.1"], value: "App-2.Latest", rule: 1, reason: null };
    expect(tryLine({ pending: false, result, error: undefined }, keys, spec)).toMatchObject({
      kind: "result",
      rule: 1,
    });
    const failed = tryLine(
      { pending: false, result: undefined, error: new ApiError(502, "upstream_error", "down") },
      keys,
      spec,
    );
    expect(failed.kind).toBe("error");
  });

  it("puts a 422's issues on the rule, field or input they are about, as the page names them", () => {
    const error = new ApiError(422, "validation_error", "mapping.rules.1.value: bad", [
      { loc: ["mapping", "rules", 1, "value"], message: "bad branch" },
      { loc: ["mapping", "rules", 0, "match"], message: "bad match" },
      { loc: ["mapping", "field"], message: "bad id" },
      { loc: ["issue"], message: "not a key" },
      { loc: [], message: "send one" },
    ]);
    const lines = tryErrorLines(error, ["a", "b"], spec);
    expect(lines).toHaveLength(5);
    expect(lines.find((l) => l.endsWith("bad branch"))).toMatch(
      new RegExp(`^${ruleWord(1)} ${ruleFieldName(spec, "value")}:`),
    );
    expect(lines.find((l) => l.endsWith("bad match"))).toMatch(
      new RegExp(`^${ruleWord(0)} ${ruleFieldName(spec, "match")}:`),
    );
    expect(lines.find((l) => l.endsWith("bad id"))).not.toBe("bad id");
    expect(lines).toContain("not a key");
    expect(lines).toContain("send one");
  });

  it("says why a preview failed for the codes it knows", () => {
    expect(tryErrorLines(new ApiError(501, "not_implemented", "x"), [], spec)).toHaveLength(1);
    const [notFound] = tryErrorLines(new ApiError(404, "ticket_not_found", "PLAT-9"), [], spec);
    expect(notFound).toContain("PLAT-9");
  });
});
