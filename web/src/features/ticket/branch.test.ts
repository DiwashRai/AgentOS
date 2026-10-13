import { describe, expect, it } from "vitest";

import { ApiError, type Schemas } from "../../api/client";
import { person, run } from "../../domain/fixtures";
import {
  branchError,
  branchErrorText,
  branchHint,
  branchLock,
  branchPatch,
  branchView,
  canSaveBranch,
  canUseDefault,
  LOCKED_ERROR,
  LOCKED_TITLE,
  lockNote,
  RAN_HINT,
  repoDefault,
  RUN_LOCK_TITLE,
} from "./branch";

type Info = Schemas["BaseBranchInfo"];

const info = (over: Partial<Info> = {}): Info => ({
  source: "repo_default",
  source_value: null,
  reason: null,
  set_by: null,
  locked: false,
  locked_by_run: null,
  ...over,
});

const repos = [
  { name: "acme/api", default_branch: "main" },
  { name: "acme/web", default_branch: "develop" },
  { name: "acme/rel", default_branch: null },
];

describe("repoDefault", () => {
  it("finds the repo's default_branch in the catalog", () => {
    expect(repoDefault("acme/web", repos)).toBe("develop");
  });
  it("is null for a no-repo ticket, an unknown repo, a repo without one or an unloaded catalog", () => {
    expect(repoDefault(null, repos)).toBeNull();
    expect(repoDefault("acme/gone", repos)).toBeNull();
    expect(repoDefault("acme/rel", repos)).toBeNull();
    expect(repoDefault("acme/api", undefined)).toBeNull();
  });
});

describe("branchView", () => {
  it("names a mapped branch's Jira value", () => {
    const t = {
      base_branch: "App-1.Latest",
      base_branch_info: info({ source: "mapping", source_value: "version_1.0" }),
    };
    expect(branchView(t)).toEqual({
      text: "App-1.Latest",
      named: true,
      source: "from Jira · version_1.0",
      missing: false,
    });
    const bare = { ...t, base_branch_info: info({ source: "mapping" }) };
    expect(branchView(bare).source).toBe("from Jira");
  });
  it("says who set it by hand, by first name", () => {
    const t = {
      base_branch: "release/2.1",
      base_branch_info: info({ source: "manual", set_by: person(2, "Sam Lee") }),
    };
    expect(branchView(t).source).toBe("set by Sam");
    expect(branchView({ ...t, base_branch_info: info({ source: "manual" }) }).source).toBe(
      "set by hand",
    );
  });
  it("marks the repo's default from the server's source, not by comparing names", () => {
    expect(branchView({ base_branch: "main", base_branch_info: info() }).source).toBe(
      "repo default",
    );
    const own = { base_branch: "main", base_branch_info: info({ source: "manual" }) };
    expect(branchView(own).source).toBe("set by hand");
  });
  it("has no source line without the info", () => {
    expect(branchView({ base_branch: "main", base_branch_info: null })).toEqual({
      text: "main",
      named: true,
      source: null,
      missing: false,
    });
  });
  it("says Not set, with the server's reason, as missing", () => {
    const t = {
      base_branch: null,
      base_branch_info: info({
        source: "none",
        source_value: "version_3.0",
        reason: "No rule matches version_3.0",
      }),
    };
    expect(branchView(t)).toEqual({
      text: "Not set",
      named: false,
      source: "No rule matches version_3.0",
      missing: true,
    });
    expect(branchView({ base_branch: null, base_branch_info: info({ source: "none" }) })).toEqual({
      text: "Not set",
      named: false,
      source: null,
      missing: true,
    });
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
  it("sends the name when the repo has no default", () => {
    expect(branchPatch("main", null)).toEqual({ base_branch: "main" });
  });
});

describe("canUseDefault", () => {
  it("offers the repo default when the repo has one and the ticket doesn't follow it", () => {
    expect(canUseDefault(info({ source: "manual" }), "main")).toBe(true);
    expect(canUseDefault(info({ source: "mapping" }), "main")).toBe(true);
    expect(canUseDefault(info({ source: "none" }), "main")).toBe(true);
    expect(canUseDefault(null, "main")).toBe(true);
  });
  it("hides it when the ticket follows the default, or the repo has none", () => {
    expect(canUseDefault(info({ source: "repo_default" }), "main")).toBe(false);
    expect(canUseDefault(info({ source: "manual" }), null)).toBe(false);
  });
});

describe("branchLock / lockNote / branchHint", () => {
  it("locks during a run", () => {
    expect(branchLock({ active_run_id: 7, base_branch_info: info() })).toBe(RUN_LOCK_TITLE);
    expect(branchLock({ active_run_id: null, base_branch_info: info() })).toBeNull();
    expect(branchLock({ active_run_id: null, base_branch_info: null })).toBeNull();
  });
  it("prefers the server's lock", () => {
    const locked = info({ locked: true, locked_by_run: 12 });
    expect(branchLock({ active_run_id: null, base_branch_info: locked })).toBe(LOCKED_TITLE);
    expect(branchLock({ active_run_id: 13, base_branch_info: locked })).toBe(LOCKED_TITLE);
  });
  it("notes the run that locked it, linking to it on the Runs tab", () => {
    const t = { key: "AOS-1", base_branch_info: info({ locked: true, locked_by_run: 12 }) };
    expect(lockNote(t)).toEqual({ text: "Locked by run #12", href: "/t/AOS-1/runs?run=12" });
    const noRun = { key: "AOS-1", base_branch_info: info({ locked: true }) };
    expect(lockNote(noRun)).toEqual({ text: "Locked", href: null });
    expect(lockNote({ key: "AOS-1", base_branch_info: info() })).toBeNull();
    expect(lockNote({ key: "AOS-1", base_branch_info: null })).toBeNull();
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
  it("says the branch is locked for base_branch_locked", () => {
    expect(branchErrorText(new ApiError(409, "base_branch_locked", "run 12"))).toBe(LOCKED_ERROR);
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
