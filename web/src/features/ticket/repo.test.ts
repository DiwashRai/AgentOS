import { describe, expect, it } from "vitest";

import { ApiError, type Schemas } from "../../api/client";
import {
  NO_REPO,
  NO_REPO_WARNING,
  REPO_LOCKED_ERROR,
  REPO_LOCKED_TITLE,
  REPO_RUN_LOCK_TITLE,
  repoErrorText,
  repoLock,
  repoOptions,
  repoPatch,
  repoValue,
  repoWarning,
} from "./repo";

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
  { name: "acme/web", default_branch: "develop" },
  { name: "acme/api", default_branch: "main" },
  { name: "acme/rel", default_branch: null },
];

describe("repoValue / repoPatch", () => {
  it("maps no repo to the empty value and back to null", () => {
    expect(repoValue(null)).toBe(NO_REPO);
    expect(repoValue("acme/api")).toBe("acme/api");
    expect(repoPatch("")).toEqual({ repo: null });
    expect(repoPatch("acme/web")).toEqual({ repo: "acme/web" });
  });
});

describe("repoOptions", () => {
  it("puts No repo first, then the catalog by name", () => {
    expect(repoOptions("acme/api", repos)).toEqual([
      { value: "", label: "No repo" },
      { value: "acme/api", label: "acme/api" },
      { value: "acme/rel", label: "acme/rel" },
      { value: "acme/web", label: "acme/web" },
    ]);
  });
  it("keeps the ticket's repo when it left the catalog", () => {
    expect(repoOptions("acme/old", repos).map((o) => o.value)).toEqual([
      "",
      "acme/api",
      "acme/old",
      "acme/rel",
      "acme/web",
    ]);
  });
  it("keeps the ticket's repo before the config loads", () => {
    expect(repoOptions("acme/api", undefined).map((o) => o.value)).toEqual(["", "acme/api"]);
    expect(repoOptions(null, undefined).map((o) => o.value)).toEqual([""]);
  });
});

describe("repoLock", () => {
  it("is null at rest", () => {
    expect(repoLock({ active_run_id: null, base_branch_info: info() })).toBeNull();
    expect(repoLock({ active_run_id: null, base_branch_info: null })).toBeNull();
  });
  it("names an active run", () => {
    expect(repoLock({ active_run_id: 4, base_branch_info: null })).toBe(REPO_RUN_LOCK_TITLE);
  });
  it("puts the server's lock first", () => {
    expect(
      repoLock({ active_run_id: 4, base_branch_info: info({ locked: true, locked_by_run: 3 }) }),
    ).toBe(REPO_LOCKED_TITLE);
    expect(
      repoLock({ active_run_id: null, base_branch_info: info({ locked: true, locked_by_run: 3 }) }),
    ).toBe(REPO_LOCKED_TITLE);
  });
});

describe("repoWarning", () => {
  const following = {
    repo_name: "acme/api",
    base_branch: "main",
    base_branch_info: info(),
  };
  const manual = {
    repo_name: "acme/api",
    base_branch: "feat/x",
    base_branch_info: info({ source: "manual" }),
  };
  const mapped = {
    repo_name: "acme/api",
    base_branch: "version_1.0",
    base_branch_info: info({ source: "mapping", source_value: "1.0" }),
  };

  it("needs no confirm for the same repo", () => {
    expect(repoWarning(manual, "acme/api", repos)).toBeNull();
    expect(repoWarning({ ...manual, repo_name: null }, "", repos)).toBeNull();
  });
  it("warns about stages and the base branch for No repo", () => {
    expect(repoWarning(following, "", repos)).toBe(NO_REPO_WARNING);
    expect(repoWarning(manual, "", repos)).toBe(NO_REPO_WARNING);
  });
  it("needs no confirm when the ticket follows the default and the new repo has one", () => {
    expect(repoWarning(following, "acme/web", repos)).toBeNull();
  });
  it("says a default-less repo needs a base branch", () => {
    expect(repoWarning(following, "acme/rel", repos)).toBe(
      "acme/rel has no default branch, so set one before dispatch.",
    );
    const none = { repo_name: null, base_branch: null, base_branch_info: null };
    expect(repoWarning(none, "acme/rel", repos)).toBe(
      "acme/rel has no default branch, so set one before dispatch.",
    );
    expect(repoWarning(none, "acme/web", repos)).toBeNull();
  });
  it("says a hand-set or mapped branch goes back to the new default", () => {
    expect(repoWarning(manual, "acme/web", repos)).toBe(
      "feat/x goes back to acme/web's default, develop.",
    );
    expect(repoWarning(mapped, "acme/web", repos)).toBe(
      "version_1.0 goes back to acme/web's default, develop.",
    );
  });
  it("says a hand-set branch is dropped for a default-less repo", () => {
    expect(repoWarning(manual, "acme/rel", repos)).toBe(
      "feat/x is dropped: acme/rel has no default branch, so set one before dispatch.",
    );
  });
  it("needs no confirm when the own branch is the new repo's default", () => {
    expect(repoWarning({ ...manual, base_branch: "develop" }, "acme/web", repos)).toBeNull();
  });
  it("treats a blank mapped branch as nothing to lose", () => {
    const blank = {
      repo_name: "acme/api",
      base_branch: null,
      base_branch_info: info({ source: "none", reason: "No rule matches 3.0" }),
    };
    expect(repoWarning(blank, "acme/web", repos)).toBeNull();
  });
  it("needs no confirm for a repo missing from the catalog (the server refuses it)", () => {
    expect(repoWarning(manual, "acme/gone", repos)).toBeNull();
    expect(repoWarning(manual, "acme/web", undefined)).toBeNull();
  });
});

describe("repoErrorText", () => {
  it("names the repo for run_active", () => {
    expect(repoErrorText(new ApiError(409, "run_active", "x"))).toBe(REPO_RUN_LOCK_TITLE);
  });
  it("says the repo is locked for base_branch_locked", () => {
    expect(repoErrorText(new ApiError(409, "base_branch_locked", "run 12"))).toBe(
      REPO_LOCKED_ERROR,
    );
  });
  it("falls back to the header's action text", () => {
    expect(repoErrorText(new ApiError(422, "validation_error", "repo: unknown"))).toBe(
      "repo: unknown",
    );
    expect(repoErrorText(new ApiError(409, "stale_version", "x"))).toBe(
      "Changed by someone else · reload and try again",
    );
  });
});
