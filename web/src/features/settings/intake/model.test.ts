import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import {
  addQuery,
  formOf,
  issueCount,
  JQL_MAX,
  MAX_QUERIES,
  moveQuery,
  NAME_MAX,
  patchQuery,
  previewLine,
  removeQuery,
  sameForm,
  toBody,
  validate,
  type IntakePreview,
  type PreviewItem,
  type PreviewState,
  type QueryRow,
} from "./model";

const SAVED = {
  queries: [
    { id: "ready", name: "Ready for agents", jql: "project = PLAT AND labels = agent" },
    { id: "bugs", name: "Bugs", jql: "project = PLAT AND type = Bug" },
  ],
};

function row(over: Partial<QueryRow> = {}): QueryRow {
  return { key: "id:x", id: "x", name: "X", jql: "project = X", ...over };
}

function item(key: string): PreviewItem {
  return {
    key,
    title: `Issue ${key}`,
    type: "bug",
    priority: "normal",
    status: "To Do",
    assignee: null,
    updated_at: "2026-10-12T08:00:00Z",
    url: `https://jira.example/browse/${key}`,
  };
}

describe("form ↔ body", () => {
  it("keys saved queries by id and drops the keys from the body", () => {
    const form = formOf(SAVED);
    expect(form.map((r) => r.key)).toEqual(["id:ready", "id:bugs"]);
    const body = toBody([...form, { key: "new:1", id: null, name: "  New ", jql: " a = b\n" }], 7);
    expect(body).toEqual({
      base_version: 7,
      queries: [
        { id: "ready", name: "Ready for agents", jql: "project = PLAT AND labels = agent" },
        { id: "bugs", name: "Bugs", jql: "project = PLAT AND type = Bug" },
        { id: null, name: "New", jql: "a = b" },
      ],
    });
  });

  it("sameForm ignores the client keys but not order or content", () => {
    const a = formOf(SAVED);
    const relabelled = a.map((r) => ({ ...r, key: `other:${r.key}` }));
    expect(sameForm(a, relabelled)).toBe(true);
    expect(sameForm(a, moveQuery(a, 0, 1))).toBe(false);
    expect(sameForm(a, patchQuery(a, 1, { jql: "x" }))).toBe(false);
    expect(sameForm(a, a.slice(1))).toBe(false);
  });
});

describe("list edits", () => {
  it("adds empty new queries with fresh keys, even after one is removed", () => {
    const one = addQuery(formOf(SAVED));
    expect(one[2]).toEqual({ key: "new:1", id: null, name: "", jql: "" });
    const two = addQuery(one);
    expect(two[3]?.key).toBe("new:2");
    expect(addQuery(removeQuery(two, 2)).map((r) => r.key)).toEqual([
      "id:ready",
      "id:bugs",
      "new:2",
      "new:3",
    ]);
  });

  it("moves within bounds only and removes by index", () => {
    const form = formOf(SAVED);
    expect(moveQuery(form, 1, -1).map((r) => r.id)).toEqual(["bugs", "ready"]);
    expect(moveQuery(form, 0, -1)).toBe(form);
    expect(moveQuery(form, 1, 1)).toBe(form);
    expect(removeQuery(form, 0).map((r) => r.id)).toEqual(["bugs"]);
  });

  it("patches one row", () => {
    const form = patchQuery(formOf(SAVED), 0, { name: "Ready" });
    expect(form[0]?.name).toBe("Ready");
    expect(form[1]?.name).toBe("Bugs");
  });
});

describe("validate", () => {
  it("needs a name and a JQL, trimmed, within the server's limits", () => {
    const issues = validate([
      row({ name: "  ", jql: "\n" }),
      row({ name: "n".repeat(NAME_MAX + 1), jql: "j".repeat(JQL_MAX + 1) }),
      row({ name: ` ${"n".repeat(NAME_MAX)} `, jql: "ok" }),
    ]);
    expect(Object.keys(issues.rows[0] ?? {})).toEqual(["name", "jql"]);
    expect(issues.rows[1]).toEqual({
      name: `At most ${String(NAME_MAX)} characters`,
      jql: `At most ${String(JQL_MAX)} characters`,
    });
    expect(issues.rows[2]).toEqual({});
    expect(issueCount(issues)).toBe(4);
  });

  it("caps the number of queries", () => {
    const many = Array.from({ length: MAX_QUERIES + 1 }, (_, i) =>
      row({ key: `new:${String(i)}` }),
    );
    const issues = validate(many);
    expect(issues.form).toHaveLength(1);
    expect(issueCount(issues)).toBe(1);
    expect(validate(many.slice(1)).form).toEqual([]);
  });
});

describe("previewLine", () => {
  const JQL = "project = PLAT";
  const done = (data: IntakePreview): PreviewState => ({
    ran: JQL,
    pending: false,
    data,
    error: null,
  });

  it("shows nothing until tested, and nothing once the JQL changed", () => {
    expect(
      previewLine({ ran: undefined, pending: false, data: undefined, error: null }, JQL),
    ).toEqual({ kind: "none" });
    const state = done({ total: 0, error: null, items: [] });
    expect(previewLine(state, `${JQL} AND x = y`)).toEqual({ kind: "none" });
    // Whitespace around the JQL is not a change (the test sends it trimmed).
    expect(previewLine(state, `  ${JQL}\n`).kind).toBe("empty");
  });

  it("pending, then the count with up to 5 items", () => {
    expect(previewLine({ ran: JQL, pending: true, data: undefined, error: null }, JQL)).toEqual({
      kind: "pending",
    });
    const items = ["A-1", "A-2", "A-3", "A-4", "A-5", "A-6"].map(item);
    const line = previewLine(done({ total: 12, error: null, items }), JQL);
    expect(line).toMatchObject({ kind: "items", count: "12 to take in", more: "first 5 shown" });
    if (line.kind !== "items") throw new Error("expected items");
    expect(line.items.map((i) => i.key)).toEqual(["A-1", "A-2", "A-3", "A-4", "A-5"]);
    expect(
      previewLine(done({ total: 2, error: null, items: items.slice(0, 2) }), JQL),
    ).toMatchObject({ count: "2 to take in", more: null });
  });

  it("Jira's error text, or the request's own failure", () => {
    expect(
      previewLine(done({ total: 0, error: "Field 'x' does not exist", items: [] }), JQL),
    ).toEqual({ kind: "error", text: "Field 'x' does not exist" });
    const failed: PreviewState = {
      ran: JQL,
      pending: false,
      data: undefined,
      error: new ApiError(403, "forbidden", "Admins only"),
    };
    expect(previewLine(failed, JQL)).toEqual({ kind: "error", text: "Admins only" });
  });

  it("nothing to take in", () => {
    expect(previewLine(done({ total: 0, error: null, items: [] }), JQL)).toEqual({ kind: "empty" });
  });
});
