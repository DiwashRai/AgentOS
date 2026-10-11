import { describe, expect, it } from "vitest";

import { hasErrors, initialForm, toCreateBody, trapIndex, validate, TITLE_MAX } from "./form";

const defaults = { repoUrl: "https://github.com/o/r", baseBranch: "main", meId: 4 };

describe("new ticket form", () => {
  it("prefills repo, branch and owner from config and me", () => {
    const f = initialForm(defaults);
    expect(f).toMatchObject({
      repoUrl: "https://github.com/o/r",
      baseBranch: "main",
      ownerId: "4",
    });
    expect(f).toMatchObject({ type: "task", priority: "normal", workspace: "repo" });
    expect(initialForm({ repoUrl: null, baseBranch: undefined, meId: null })).toMatchObject({
      repoUrl: "",
      baseBranch: "",
      ownerId: "",
    });
  });

  it("requires a title of at most 200 characters", () => {
    const f = initialForm(defaults);
    expect(validate(f).title).toBeDefined();
    expect(validate({ ...f, title: "   " }).title).toBeDefined();
    expect(validate({ ...f, title: "x".repeat(TITLE_MAX + 1) }).title).toBeDefined();
    expect(hasErrors(validate({ ...f, title: `  ${"x".repeat(TITLE_MAX)}  ` }))).toBe(false);
  });

  it("rejects a repo URL with spaces only when the workspace is a repo", () => {
    const f = { ...initialForm(defaults), title: "T", repoUrl: "not a url" };
    expect(validate(f).repoUrl).toBeDefined();
    expect(validate({ ...f, workspace: "none" }).repoUrl).toBeUndefined();
  });

  it("builds the create body", () => {
    const f = { ...initialForm(defaults), title: "  Fix it ", description: "Why\n\nHow" };
    expect(toCreateBody(f)).toEqual({
      title: "Fix it",
      description: "Why\n\nHow",
      type: "task",
      priority: "normal",
      workspace_mode: "repo",
      repo_url: "https://github.com/o/r",
      base_branch: "main",
      owner_id: 4,
    });
  });

  it("sends nulls for empty repo fields, no workspace and unassigned", () => {
    const f = { ...initialForm(defaults), title: "T", repoUrl: " ", baseBranch: "", ownerId: "" };
    expect(toCreateBody(f)).toMatchObject({ repo_url: null, base_branch: null, owner_id: null });
    const none = { ...initialForm(defaults), title: "T", workspace: "none" as const };
    expect(toCreateBody(none)).toMatchObject({
      workspace_mode: "none",
      repo_url: null,
      base_branch: null,
    });
  });
});

describe("focus trap", () => {
  it("wraps Tab and Shift+Tab", () => {
    expect(trapIndex(0, 3, false)).toBe(1);
    expect(trapIndex(2, 3, false)).toBe(0);
    expect(trapIndex(0, 3, true)).toBe(2);
    expect(trapIndex(-1, 3, false)).toBe(0);
    expect(trapIndex(-1, 3, true)).toBe(2);
    expect(trapIndex(0, 0, false)).toBe(-1);
  });
});
