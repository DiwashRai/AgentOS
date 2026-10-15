import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import {
  fromForm,
  patchQuery,
  previewLine,
  queryLabels,
  sameForm,
  toForm,
  type IntakePreview,
  type PreviewItem,
  type PreviewState,
} from "./model";

const SAVED = {
  queries: [
    { id: "ready", name: "Ready for agents", jql: "project = PLAT AND labels = agent" },
    { id: "bugs", name: "Bugs", jql: "project = PLAT AND type = Bug" },
  ],
};

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

describe("form ↔ value", () => {
  it("round-trips the queries without the row keys", () => {
    expect(fromForm(toForm(SAVED))).toEqual(SAVED);
  });

  it("sameForm ignores the row keys but not order or content", () => {
    const a = toForm(SAVED);
    expect(sameForm(a, toForm(SAVED))).toBe(true);
    expect(sameForm(a, { queries: [...a.queries].reverse() })).toBe(false);
    expect(sameForm(a, patchQuery(a, a.queries[1]?.key ?? "", { jql: "x" }))).toBe(false);
  });
});

describe("queryLabels", () => {
  it("names every row uniquely, by name, then by place where a name is blank or shared", () => {
    const form = toForm({
      queries: [
        { id: "a", name: "Ready", jql: "" },
        { id: null, name: " ", jql: "" },
        { id: "b", name: "Bugs", jql: "" },
        { id: "c", name: "ready ", jql: "" },
      ],
    });
    const labels = queryLabels(form.queries);
    expect(new Set(labels).size).toBe(4);
    expect(labels[2]).toBe("Bugs");
    expect(labels[0]).toContain("Ready");
    expect(labels[3]).toContain("ready");
    expect(labels.every((l) => l.trim() !== "")).toBe(true);
  });
});

describe("previewLine", () => {
  const done = (result: IntakePreview): PreviewState => ({
    pending: false,
    result,
    error: undefined,
  });

  it("nothing before a run, then pending", () => {
    expect(previewLine({ pending: false, result: undefined, error: undefined }).kind).toBe("none");
    expect(previewLine({ pending: true, result: undefined, error: undefined }).kind).toBe(
      "pending",
    );
  });

  it("the count over the items the server sent", () => {
    const items = ["A-1", "A-2", "A-3"].map(item);
    const line = previewLine(done({ total: 12, error: null, items }));
    expect(line).toMatchObject({ kind: "items", count: "12 to take in", more: "first 3 shown" });
    expect(previewLine(done({ total: 3, error: null, items }))).toMatchObject({ more: null });
    expect(previewLine(done({ total: 0, error: null, items: [] })).kind).toBe("empty");
  });

  it("Jira's error, the server's issues with the JQL, or the request's own failure", () => {
    expect(previewLine(done({ total: 0, error: "Field 'x' does not exist", items: [] }))).toEqual({
      kind: "error",
      text: "Field 'x' does not exist",
    });

    const refused = new ApiError(422, "validation_error", "jql: too long", [
      { loc: ["jql"], message: "too long" },
    ]);
    expect(previewLine({ pending: false, result: undefined, error: refused })).toEqual({
      kind: "error",
      text: "too long",
    });

    const forbidden = new ApiError(403, "forbidden", "Admins only");
    expect(previewLine({ pending: false, result: undefined, error: forbidden })).toEqual({
      kind: "error",
      text: "Admins only",
    });
  });
});
