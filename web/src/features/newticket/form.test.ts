import { describe, expect, it } from "vitest";

import {
  NEEDS_BRANCH_NOTE,
  NEEDS_BRANCH_PLACEHOLDER,
  NO_REPO,
  branchNote,
  branchPlaceholder,
  needsBranch,
  hasErrors,
  initialForm,
  repoOptions,
  toCreateBody,
  validate,
  TITLE_MAX,
} from "./form";

const defaults = { repo: "agentos", meId: 4 };
const repos = [
  { name: "agentos", url: "https://github.com/o/agentos", default_branch: "main" },
  { name: "scratch", url: "https://github.com/o/scratch", default_branch: "trunk" },
  { name: "releases", url: "https://github.com/o/releases", default_branch: null },
  { name: "blank", url: "https://github.com/o/blank", default_branch: "" },
];

describe("new ticket form", () => {
  it("prefills the default repo and owner from config and me", () => {
    const f = initialForm(defaults);
    expect(f).toMatchObject({ repo: "agentos", baseBranch: "", ownerId: "4" });
    expect(f).toMatchObject({ type: "task", priority: "normal" });
    expect(initialForm({ repo: null, meId: null })).toMatchObject({ repo: NO_REPO, ownerId: "" });
  });

  it("offers the catalog by name, then No repo", () => {
    expect(repoOptions(repos.slice(0, 2))).toEqual([
      { value: "agentos", label: "agentos" },
      { value: "scratch", label: "scratch" },
      { value: NO_REPO, label: "No repo" },
    ]);
    expect(repoOptions([])).toEqual([{ value: NO_REPO, label: "No repo" }]);
  });

  it("shows the picked repo's default branch as the placeholder", () => {
    expect(branchPlaceholder(repos, "scratch")).toBe("trunk");
    expect(branchPlaceholder(repos, NO_REPO)).toBe("");
    expect(branchPlaceholder(repos, "gone")).toBe("");
  });

  it("asks for a branch when the repo has no default, without blocking creation", () => {
    expect(needsBranch(repos, "releases")).toBe(true);
    expect(needsBranch(repos, "blank")).toBe(true);
    expect(needsBranch(repos, "agentos")).toBe(false);
    expect(needsBranch(repos, NO_REPO)).toBe(false);
    expect(needsBranch(repos, "gone")).toBe(false);
    expect(branchPlaceholder(repos, "releases")).toBe(NEEDS_BRANCH_PLACEHOLDER);
    expect(branchNote(repos, "releases", " ")).toBe(NEEDS_BRANCH_NOTE);
    expect(branchNote(repos, "releases", "App-1.Latest")).toBeUndefined();
    expect(branchNote(repos, "agentos", "")).toBeUndefined();
    const f = { ...initialForm(defaults), title: "T", repo: "releases" };
    expect(hasErrors(validate(f))).toBe(false);
    expect(toCreateBody(f)).toMatchObject({ repo: "releases", base_branch: null });
  });

  it("requires a title of at most 200 characters", () => {
    const f = initialForm(defaults);
    expect(validate(f).title).toBeDefined();
    expect(validate({ ...f, title: "   " }).title).toBeDefined();
    expect(validate({ ...f, title: "x".repeat(TITLE_MAX + 1) }).title).toBeDefined();
    expect(hasErrors(validate({ ...f, title: `  ${"x".repeat(TITLE_MAX)}  ` }))).toBe(false);
  });

  it("rejects a base branch with spaces only when a repo is picked", () => {
    const f = { ...initialForm(defaults), title: "T", baseBranch: "my branch" };
    expect(validate(f).baseBranch).toBeDefined();
    expect(validate({ ...f, repo: NO_REPO }).baseBranch).toBeUndefined();
  });

  it("builds the create body", () => {
    const f = {
      ...initialForm(defaults),
      title: "  Fix it ",
      description: "Why\n\nHow",
      baseBranch: " dev ",
    };
    expect(toCreateBody(f)).toEqual({
      title: "Fix it",
      description: "Why\n\nHow",
      type: "task",
      priority: "normal",
      repo: "agentos",
      base_branch: "dev",
      owner_id: 4,
      workflow_id: null,
    });
  });

  it("sends the picked workflow", () => {
    const f = { ...initialForm(defaults), title: "T" };
    expect(toCreateBody(f, 7)).toMatchObject({ workflow_id: 7 });
  });

  it("sends nulls for a blank branch, no repo and unassigned", () => {
    const f = { ...initialForm(defaults), title: "T", baseBranch: " ", ownerId: "" };
    expect(toCreateBody(f)).toMatchObject({ repo: "agentos", base_branch: null, owner_id: null });
    const none = { ...initialForm(defaults), title: "T", repo: NO_REPO, baseBranch: "dev" };
    expect(toCreateBody(none)).toMatchObject({ repo: null, base_branch: null });
  });
});
