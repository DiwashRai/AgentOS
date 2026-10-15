import { describe, expect, it } from "vitest";

import {
  claimHint,
  defaultRepoOptions,
  duration,
  newRepoRow,
  repoKeys,
  reposFromForm,
  reposToForm,
  sameRepos,
  spaceFromForm,
  spaceToForm,
  splitProjectKeys,
  urlHint,
  type ReposDoc,
  type SpaceDoc,
} from "./model";

const SPACE: SpaceDoc = {
  name: "AgentOS",
  key_prefix: "AOS",
  jira_projects: ["SCRUM", "PLAT"],
  default_repo: "agentos",
  claim_timeout_s: 900,
};

const REPOS: ReposDoc = {
  repos: [
    { name: "agentos", url: "https://github.com/o/agentos.git", default_branch: "main" },
    { name: "scratch", url: "git@github.com:o/scratch.git", default_branch: null },
  ],
};

describe("Space form", () => {
  it("edits the timeout as text and sends the value back trimmed", () => {
    const f = spaceToForm(SPACE);
    expect(f.claim_timeout_s).toBe("900");
    expect(spaceFromForm({ ...f, name: "  Home  ", claim_timeout_s: " 60 " })).toEqual({
      ...SPACE,
      name: "Home",
      claim_timeout_s: 60,
    });
  });

  it("splits typed or pasted project keys and upper-cases them, refusing none", () => {
    expect(splitProjectKeys("scrum, plat;  ops\nSCRUM a-b")).toEqual([
      "SCRUM",
      "PLAT",
      "OPS",
      "SCRUM",
      "A-B",
    ]);
    expect(splitProjectKeys(" , ")).toEqual([]);
  });

  it("offers None, the saved repos, and a current default that left them", () => {
    const values = (current: string | null) =>
      defaultRepoOptions(["agentos", "scratch"], current).map((o) => o.value);
    expect(values("agentos")).toEqual(["", "agentos", "scratch"]);
    expect(values("gone")).toEqual(["", "agentos", "scratch", "gone"]);
  });

  it("reads the claim timeout as words", () => {
    expect(duration(60)).toBe("1 minute");
    expect(duration(900)).toBe("15 minutes");
    expect(duration(86400)).toBe("24 hours");
    expect(duration(5400)).toBe("1 h 30 min");
    expect(duration(90)).toBe("1 min 30 s");
    expect(claimHint("900")).toMatch(/\(15 minutes\)/);
    expect(claimHint("")).not.toMatch(/\(/);
  });
});

describe("Repos form", () => {
  it("round-trips the catalog: no default branch is blank in the form and null out", () => {
    const f = reposToForm(REPOS);
    expect(f.repos.map((r) => [r.fresh, r.default_branch])).toEqual([
      [false, "main"],
      [false, ""],
    ]);
    expect(reposFromForm(f)).toEqual(REPOS);
    const blanked = {
      repos: f.repos.map((r) => ({ ...r, url: ` ${r.url} `, default_branch: " " })),
    };
    expect(reposFromForm(blanked).repos.map((r) => [r.url, r.default_branch])).toEqual([
      ["https://github.com/o/agentos.git", null],
      ["git@github.com:o/scratch.git", null],
    ]);
  });

  it("keys every row uniquely and sends the keys in row order", () => {
    const f = reposToForm(REPOS);
    const added = { repos: [...f.repos, newRepoRow()] };
    const keys = repoKeys(added).repos ?? [];
    expect(new Set(keys).size).toBe(3);
    expect(keys).toEqual(added.repos.map((r) => r.key));
  });

  it("compares rows by content and order, not by their client keys", () => {
    const a = reposToForm(REPOS);
    const b = reposToForm(REPOS);
    expect(sameRepos(a, b)).toBe(true);
    expect(sameRepos(a, { repos: [...a.repos].reverse() })).toBe(false);
    expect(sameRepos(a, { repos: a.repos.map((r) => ({ ...r, url: `${r.url} ` })) })).toBe(false);
  });

  it("hints (doesn't fail) when a runner couldn't clone the URL", () => {
    expect(urlHint("https://github.com/o/r")).toBeUndefined();
    expect(urlHint("")).toBeUndefined();
    expect(urlHint("git@github.com:o/r.git")).toMatch(/https/);
  });
});
