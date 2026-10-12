import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { run } from "../../domain/fixtures";
import {
  branchError,
  branchErrorText,
  branchHint,
  branchLock,
  branchPatch,
  branchView,
  canSaveBranch,
  canUseDefault,
  RAN_HINT,
  repoDefault,
  RUN_LOCK_TITLE,
} from "./branch";

const repos = [
  { name: "acme/api", default_branch: "main" },
  { name: "acme/web", default_branch: "develop" },
];

describe("repoDefault", () => {
  it("finds the repo's default_branch in the catalog", () => {
    expect(repoDefault("acme/web", repos)).toBe("develop");
  });
  it("is null for a no-repo ticket, an unknown repo or an unloaded catalog", () => {
    expect(repoDefault(null, repos)).toBeNull();
    expect(repoDefault("acme/gone", repos)).toBeNull();
    expect(repoDefault("acme/api", undefined)).toBeNull();
  });
});

describe("branchView", () => {
  it("marks the repo's default", () => {
    expect(branchView("main", "main")).toEqual({
      text: "main",
      named: true,
      suffix: "· repo default",
    });
  });
  it("shows a ticket's own branch without the suffix", () => {
    expect(branchView("release/2.1", "main")).toEqual({
      text: "release/2.1",
      named: true,
      suffix: null,
    });
  });
  it("has no suffix when the default is unknown", () => {
    expect(branchView("main", null).suffix).toBeNull();
  });
  it("says Not set when there is no branch", () => {
    expect(branchView(null, "main")).toEqual({ text: "Not set", named: false, suffix: null });
  });
});

describe("branchError / canSaveBranch", () => {
  it("validates the trimmed input with checkBranch", () => {
    expect(branchError("  release/2.1 ")).toBeUndefined();
    expect(branchError("   ")).toBe("Required.");
    expect(branchError("a..b")).toBeDefined();
    expect(branchError("feat/x.lock")).toBeDefined();
  });
  it("saves only a valid, changed value", () => {
    expect(canSaveBranch("release/2.1", "main")).toBe(true);
    expect(canSaveBranch(" main ", "main")).toBe(false);
    expect(canSaveBranch("bad..name", "main")).toBe(false);
    expect(canSaveBranch("", "main")).toBe(false);
    expect(canSaveBranch("main", null)).toBe(true);
  });
});

describe("branchPatch", () => {
  it("sends the trimmed branch", () => {
    expect(branchPatch(" release/2.1 ", "main")).toEqual({ base_branch: "release/2.1" });
  });
  it("sends null for the repo's default", () => {
    expect(branchPatch(" main", "main")).toEqual({ base_branch: null });
  });
  it("sends the name when the default is unknown", () => {
    expect(branchPatch("main", null)).toEqual({ base_branch: "main" });
  });
});

describe("canUseDefault", () => {
  it("offers the repo default only when it is known and not current", () => {
    expect(canUseDefault("release/2.1", "main")).toBe(true);
    expect(canUseDefault(null, "main")).toBe(true);
    expect(canUseDefault("main", "main")).toBe(false);
    expect(canUseDefault("release/2.1", null)).toBe(false);
  });
});

describe("branchLock / branchHint", () => {
  it("locks during a run", () => {
    expect(branchLock({ active_run_id: 7 })).toBe(RUN_LOCK_TITLE);
    expect(branchLock({ active_run_id: null })).toBeNull();
  });
  it("hints once the ticket has run", () => {
    expect(branchHint({ latest_run: run("failed") })).toBe(RAN_HINT);
    expect(branchHint({ latest_run: null })).toBeNull();
  });
});

describe("branchErrorText", () => {
  it("names the base branch for run_active", () => {
    expect(branchErrorText(new ApiError(409, "run_active", "x"))).toBe(RUN_LOCK_TITLE);
  });
  it("falls back to the header's action text", () => {
    expect(branchErrorText(new ApiError(422, "validation_error", "base_branch: bad"))).toBe(
      "base_branch: bad",
    );
    expect(branchErrorText(new ApiError(409, "stale_version", "x"))).toBe(
      "Changed by someone else · reload and try again",
    );
  });
});
