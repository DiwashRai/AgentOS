import { describe, expect, it } from "vitest";

import {
  AGENT_NAME_RULE,
  agentExists,
  agentNameFromPath,
  agentNameProblem,
  agentPath,
  agentStarter,
} from "./agents";

describe("agentNameProblem", () => {
  it("accepts lowercase letters, digits and hyphens", () => {
    for (const ok of ["reviewer", "a", "0", "code-reviewer-2", "x".repeat(64), "a-"]) {
      expect(agentNameProblem(ok)).toBeNull();
    }
  });

  it("asks for a name when empty", () => {
    expect(agentNameProblem("")).toMatch(/Give the agent a name/);
  });

  it("refuses anything else with the rule", () => {
    for (const bad of [
      "Reviewer",
      "-reviewer",
      "code_reviewer",
      "code reviewer",
      "a.b",
      "../x",
      "x".repeat(65),
    ]) {
      expect(agentNameProblem(bad)).toBe(AGENT_NAME_RULE);
    }
  });
});

describe("paths", () => {
  it("agentPath", () => {
    expect(agentPath("reviewer")).toBe(".claude/agents/reviewer.md");
  });

  it("agentNameFromPath reads back only valid agent files", () => {
    expect(agentNameFromPath(".claude/agents/reviewer.md")).toBe("reviewer");
    expect(agentNameFromPath(".claude/agents/Reviewer.md")).toBeNull();
    expect(agentNameFromPath(".claude/agents/x/y.md")).toBeNull();
    expect(agentNameFromPath(".claude/agents/reviewer.txt")).toBeNull();
    expect(agentNameFromPath("notes/reviewer.md")).toBeNull();
  });

  it("agentExists matches the exact path", () => {
    const files = [{ path: ".claude/agents/reviewer.md" }, { path: "notes/planner.md" }];
    expect(agentExists(files, "reviewer")).toBe(true);
    expect(agentExists(files, "planner")).toBe(false);
    expect(agentExists([], "reviewer")).toBe(false);
  });
});

describe("agentStarter", () => {
  it("is front matter plus a prompt, with no model line", () => {
    expect(agentStarter("reviewer")).toBe(
      [
        "---",
        "name: reviewer",
        "description: <One line: when Claude should use this agent.>",
        "---",
        "You are reviewer. <What this agent does and how it works.>",
        "",
      ].join("\n"),
    );
    expect(agentStarter("reviewer")).not.toMatch(/model:/);
  });
});
