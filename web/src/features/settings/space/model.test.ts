import { describe, expect, it } from "vitest";

import {
  addProjectKeys,
  checkBranch,
  checkRepoUrl,
  claimHint,
  duration,
  newRepoRow,
  openTickets,
  removeBlock,
  repoIssueCount,
  repoRows,
  reposBody,
  sameRepoRows,
  sameSpaceForm,
  spaceBody,
  spaceForm,
  urlHint,
  validateRepos,
  validateSpace,
  type RepoRow,
  type RepoSettings,
  type SpaceForm,
  type SpaceSettings,
} from "./model";

const META = { version: 3, updated_at: "2026-10-12T09:00:00Z", updated_by: null };

const SPACE: SpaceSettings = {
  ...META,
  name: "AgentOS",
  key_prefix: "AOS",
  jira_projects: ["SCRUM", "PLAT"],
  default_repo: "agentos",
  claim_timeout_s: 900,
};

const REPOS: RepoSettings = {
  ...META,
  repos: [
    {
      name: "agentos",
      url: "https://github.com/o/agentos.git",
      default_branch: "main",
      open_ticket_count: 2,
    },
    {
      name: "scratch",
      url: "git@github.com:o/scratch.git",
      default_branch: "dev",
      open_ticket_count: 0,
    },
  ],
};

const ctx = { jiraAuth: true, repoNames: ["agentos", "scratch"] };
const form = (over: Partial<SpaceForm> = {}): SpaceForm => ({ ...spaceForm(SPACE), ...over });

describe("Space form", () => {
  it("round-trips the saved document into a PUT body", () => {
    const f = spaceForm(SPACE);
    expect(f.claim_timeout_s).toBe("900");
    expect(spaceBody({ ...f, name: "  Home  " }, 3)).toEqual({
      name: "Home",
      key_prefix: "AOS",
      jira_projects: ["SCRUM", "PLAT"],
      default_repo: "agentos",
      claim_timeout_s: 900,
      base_version: 3,
    });
  });

  it("compares every field, the project order included", () => {
    expect(sameSpaceForm(form(), form())).toBe(true);
    expect(sameSpaceForm(form(), form({ jira_projects: ["PLAT", "SCRUM"] }))).toBe(false);
    expect(sameSpaceForm(form(), form({ default_repo: null }))).toBe(false);
    expect(sameSpaceForm(form(), form({ claim_timeout_s: "901" }))).toBe(false);
  });

  it("accepts the saved settings", () => {
    expect(validateSpace(form(), ctx)).toEqual({});
  });

  it("checks the name and key prefix", () => {
    expect(validateSpace(form({ name: "  " }), ctx).name).toBe("Required.");
    expect(validateSpace(form({ name: "x".repeat(101) }), ctx).name).toMatch(/100/);
    for (const bad of ["", "aos", "1AB", "ABCDEFGHIJK", "A-B"]) {
      expect(validateSpace(form({ key_prefix: bad }), ctx).key_prefix).toMatch(/capital/);
    }
    expect(validateSpace(form({ key_prefix: "A" }), ctx).key_prefix).toBeUndefined();
  });

  it("refuses a key prefix that is one of the Jira projects, and says why", () => {
    const issue = validateSpace(form({ key_prefix: "SCRUM" }), ctx).key_prefix;
    expect(issue).toMatch(/SCRUM is also a Jira project/);
    expect(issue).toMatch(/SCRUM-1/);
    expect(issue).toMatch(/imported/);
  });

  it("needs a project while sign-in is with Jira, not with dev auth", () => {
    const none = form({ jira_projects: [] });
    expect(validateSpace(none, ctx).jira_projects).toMatch(/At least one/);
    expect(validateSpace(none, { ...ctx, jiraAuth: false }).jira_projects).toBeUndefined();
  });

  it("flags bad and repeated project keys", () => {
    expect(validateSpace(form({ jira_projects: ["S"] }), ctx).jira_projects).toMatch(/isn't/);
    expect(validateSpace(form({ jira_projects: ["AB", "AB"] }), ctx).jira_projects).toMatch(
      /twice/,
    );
  });

  it("allows no default repo, but not one outside the saved catalog", () => {
    expect(validateSpace(form({ default_repo: null }), ctx).default_repo).toBeUndefined();
    expect(validateSpace(form({ default_repo: "gone" }), ctx).default_repo).toMatch(/saved repos/);
  });

  it("keeps the claim timeout a whole number between 60 and 86400", () => {
    for (const ok of ["60", "900", "86400"]) {
      expect(validateSpace(form({ claim_timeout_s: ok }), ctx).claim_timeout_s).toBeUndefined();
    }
    for (const bad of ["", "abc", "1.5", "-5"]) {
      expect(validateSpace(form({ claim_timeout_s: bad }), ctx).claim_timeout_s).toMatch(/whole/);
    }
    for (const out of ["59", "86401"]) {
      expect(validateSpace(form({ claim_timeout_s: out }), ctx).claim_timeout_s).toMatch(/Between/);
    }
  });
});

describe("addProjectKeys", () => {
  it("uppercases and adds several pasted keys", () => {
    expect(addProjectKeys("scrum, plat  ops", [])).toEqual({
      keys: ["SCRUM", "PLAT", "OPS"],
      error: undefined,
      rest: "",
    });
  });

  it("drops repeats and keeps invalid keys in the input", () => {
    expect(addProjectKeys("SCRUM x NEW", ["SCRUM"])).toEqual({
      keys: ["NEW"],
      error: "SCRUM is already listed.",
      rest: "X",
    });
    const bad = addProjectKeys("a-b", []);
    expect(bad.keys).toEqual([]);
    expect(bad.rest).toBe("A-B");
    expect(bad.error).toMatch(/isn't a Jira project key/);
  });

  it("stops at 50 projects", () => {
    const many = Array.from({ length: 50 }, (_, i) => `P${String(i)}`);
    expect(addProjectKeys("ONEMORE", many).error).toMatch(/At most 50/);
  });
});

describe("claim timeout words", () => {
  it("reads seconds as words", () => {
    expect(duration(60)).toBe("1 minute");
    expect(duration(900)).toBe("15 minutes");
    expect(duration(3600)).toBe("1 hour");
    expect(duration(86400)).toBe("24 hours");
    expect(duration(5400)).toBe("1 h 30 min");
    expect(duration(90)).toBe("1 min 30 s");
  });

  it("says what the timeout means", () => {
    expect(claimHint("900")).toBe(
      "A dispatched run whose worker hasn't connected after this long fails (15 minutes).",
    );
    expect(claimHint("")).not.toMatch(/\(/);
  });
});

describe("Repo rows", () => {
  const rows = repoRows(REPOS);

  it("keys saved rows by name and builds the PUT body without read-only fields", () => {
    expect(rows.map((r) => [r.key, r.fresh])).toEqual([
      ["repo:agentos", false],
      ["repo:scratch", false],
    ]);
    const edited = rows.map((r) => ({ ...r, url: ` ${r.url} ` }));
    expect(reposBody(edited, 3)).toEqual({
      repos: [
        { name: "agentos", url: "https://github.com/o/agentos.git", default_branch: "main" },
        { name: "scratch", url: "git@github.com:o/scratch.git", default_branch: "dev" },
      ],
      base_version: 3,
    });
  });

  it("allows no default branch: null comes back blank, blank goes out as null", () => {
    const none = repoRows({
      repos: [{ name: "app", url: "https://h/app", default_branch: null, open_ticket_count: 0 }],
      version: 1,
      updated_at: "2026-10-13T10:00:00Z",
      updated_by: null,
    });
    expect(none[0]?.default_branch).toBe("");
    expect(validateRepos(none).rows[0]).toEqual({});
    const blanked = rows.map((r) => ({ ...r, default_branch: "  " }));
    expect(reposBody(blanked, 1).repos.map((r) => r.default_branch)).toEqual([null, null]);
  });

  it("a new row gets an unused key and is fresh", () => {
    const a = newRepoRow(rows);
    const b = newRepoRow([...rows, a]);
    expect(a.fresh).toBe(true);
    expect(a.default_branch).toBe("main");
    expect(b.key).not.toBe(a.key);
  });

  it("compares names, URLs and branches, not keys or counts", () => {
    const relabelled = rows.map((r, i) => ({ ...r, key: `x${String(i)}`, open_ticket_count: 9 }));
    expect(sameRepoRows(rows, relabelled)).toBe(true);
    expect(sameRepoRows(rows, rows.slice(1))).toBe(false);
    const otherBranch = rows.map((r, i) => (i === 0 ? { ...r, default_branch: "dev" } : r));
    expect(sameRepoRows(rows, otherBranch)).toBe(false);
  });

  it("validates names: pattern, required, unique", () => {
    const fresh = (name: string): RepoRow => ({ ...newRepoRow(rows), name, url: "https://h/x" });
    expect(validateRepos([...rows, fresh("agentos")]).rows[2]?.name).toMatch(/twice/);
    expect(validateRepos([fresh("")]).rows[0]?.name).toBe("Required.");
    expect(validateRepos([fresh("Bad Name")]).rows[0]?.name).toMatch(/Lowercase/);
    expect(validateRepos([fresh("a.b_c-1")]).rows[0]).toEqual({});
    expect(repoIssueCount(validateRepos(rows))).toBe(0);
  });

  it("counts form and row issues", () => {
    const many = Array.from({ length: 101 }, (_, i) => ({
      ...newRepoRow([]),
      key: `k${String(i)}`,
      name: `r${String(i)}`,
      url: "",
    }));
    const v = validateRepos(many);
    expect(v.form).toEqual(["At most 100 repos."]);
    expect(repoIssueCount(v)).toBe(102);
  });
});

describe("checkRepoUrl (validate.py validate_repo_url)", () => {
  it.each([
    "https://github.com/o/r.git",
    "https://git.example.com:8443/team/r",
    "ssh://git@github.com/o/r.git",
    "ssh://host:22/o/r",
    "git@github.com:o/r.git",
    "HTTPS://github.com/o/r",
  ])("accepts %s", (url) => {
    expect(checkRepoUrl(url)).toBeUndefined();
  });

  it.each([
    ["", /Required/],
    ["http://github.com/o/r", /https:\/\//],
    ["github.com/o/r", /https:\/\//],
    ["https://github.com", /repository path/],
    ["https://github.com/", /repository path/],
    ["https://user:pw@github.com/o/r", /credentials/],
    ["ssh://bob@host/o/r", /credentials/],
    ["https://github.com/o/r?x=1", /query/],
    ["https://github.com/o r", /spaces/],
    ["-https://x/y", /start with -/],
    ["git@-host:o/r", /valid host/],
    ["git@host:-o/r", /path must not start/],
    ["https://host:abc/o/r", /port/],
    [`https://h/${"x".repeat(500)}`, /500/],
  ])("rejects %s", (url, why) => {
    expect(checkRepoUrl(url)).toMatch(why);
  });

  it("hints (doesn't fail) when a runner couldn't clone it", () => {
    expect(urlHint("https://github.com/o/r")).toBeUndefined();
    expect(urlHint("")).toBeUndefined();
    expect(urlHint("git@github.com:o/r.git")).toMatch(/https/);
  });
});

describe("checkBranch (validate.py validate_base_branch)", () => {
  it.each(["main", "release/1.2", "feature_x-y"])("accepts %s", (b) => {
    expect(checkBranch(b)).toBeUndefined();
  });

  it.each(["", "-x", "a..b", "a//b", "a/", "a.", "a.lock", "a/.b", "x/y.lock/z", "a b"])(
    "rejects %s",
    (b) => {
      expect(checkBranch(b)).toBeDefined();
    },
  );
});

describe("removeBlock", () => {
  const [agentos, scratch] = repoRows(REPOS);
  if (!agentos || !scratch) throw new Error("fixture");

  it("blocks a repo open tickets use, or the saved default", () => {
    expect(removeBlock(agentos, null)).toBe("Can't remove: 2 open tickets use it.");
    expect(removeBlock({ ...agentos, open_ticket_count: 1 }, null)).toBe(
      "Can't remove: 1 open ticket uses it.",
    );
    expect(removeBlock(scratch, "scratch")).toBe("Can't remove: it's the Space's default repo.");
    expect(removeBlock(agentos, "agentos")).toBe(
      "Can't remove: 2 open tickets use it and it's the Space's default repo.",
    );
  });

  it("allows an unused repo and any new row", () => {
    expect(removeBlock(scratch, "agentos")).toBeUndefined();
    expect(removeBlock({ ...newRepoRow([]), name: "agentos" }, "agentos")).toBeUndefined();
  });

  it("words the open ticket count", () => {
    expect(openTickets(0)).toBe("No open tickets");
    expect(openTickets(1)).toBe("1 open ticket");
    expect(openTickets(2)).toBe("2 open tickets");
  });
});
