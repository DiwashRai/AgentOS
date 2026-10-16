import { describe, expect, it } from "vitest";

import { authorLabel, isTasksFile, sortFiles, splitPath } from "./files";

describe("sortFiles", () => {
  it("pins TASKS.md first, then root files, then the rest by path", () => {
    const paths = [
      ".claude/skills/context-aos-1-fix-login/learnings.log",
      "CLAUDE.md",
      "notes/zeta.md",
      ".claude/skills/context-aos-1-fix-login/TASKS.md",
      "AGENTS.md",
      ".claude/skills/context-aos-1-fix-login/SKILL.md",
    ];
    expect(sortFiles(paths.map((path) => ({ path }))).map((f) => f.path)).toEqual([
      ".claude/skills/context-aos-1-fix-login/TASKS.md",
      "AGENTS.md",
      "CLAUDE.md",
      ".claude/skills/context-aos-1-fix-login/SKILL.md",
      ".claude/skills/context-aos-1-fix-login/learnings.log",
      "notes/zeta.md",
    ]);
  });

  it("does not mutate its input", () => {
    const input = [{ path: "b" }, { path: "a" }];
    sortFiles(input);
    expect(input.map((f) => f.path)).toEqual(["b", "a"]);
  });
});

describe("paths", () => {
  it("splitPath", () => {
    expect(splitPath("a/b/TASKS.md")).toEqual({ dir: "a/b", name: "TASKS.md" });
    expect(splitPath("AGENTS.md")).toEqual({ dir: "", name: "AGENTS.md" });
  });

  it("isTasksFile matches the basename exactly", () => {
    expect(isTasksFile("TASKS.md")).toBe(true);
    expect(isTasksFile("x/TASKS.md")).toBe(true);
    expect(isTasksFile("x/OLD_TASKS.md")).toBe(false);
    expect(isTasksFile("x/tasks.md")).toBe(false);
  });
});

describe("display", () => {
  it("authorLabel: person, else run, else template", () => {
    const person = { id: 1, display_name: "Diwash Rai", avatar_url: null };
    expect(authorLabel({ author: person, author_run_id: null })).toBe("Diwash Rai");
    expect(authorLabel({ author: null, author_run_id: 12 })).toBe("run #12");
    expect(authorLabel({ author: null, author_run_id: null })).toBe("template");
  });
});
