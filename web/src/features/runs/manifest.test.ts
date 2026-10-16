import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import {
  argChips,
  checkMeta,
  commandTokens,
  ERROR_PLACEHOLDER,
  FEEDBACK_PLACEHOLDER,
  filePaths,
  laterCalls,
  laterLabel,
  manifestErrorText,
  PROMPT_PLACEHOLDER,
  promptIndex,
  shellWord,
  stageCall,
  timeoutText,
  type Manifest,
  type ManifestStage,
} from "./manifest";

const PROMPT = "Review the change.\nKeep it short.";

const CLAUDE: ManifestStage = {
  id: "review",
  name: "Review",
  kind: "agent",
  timeout_s: 1800,
  check: false,
  verdict_repairs: 0,
  argv: [
    "claude",
    "-p",
    "--output-format",
    "stream-json",
    "--model",
    "claude-haiku-5-5",
    "--agent",
    "reviewer",
    "--effort",
    "high",
  ],
  stdin: PROMPT,
  command: null,
};

const COPILOT: ManifestStage = {
  id: "build",
  name: "Build",
  kind: "agent",
  timeout_s: 3600,
  check: false,
  verdict_repairs: 0,
  argv: ["copilot", "--allow-all", "--model", "gpt-5", "-p", PROMPT],
  stdin: null,
  command: null,
};

const SHELL: ManifestStage = {
  id: "test",
  name: "Test",
  kind: "shell",
  timeout_s: 600,
  check: false,
  verdict_repairs: 0,
  argv: null,
  stdin: null,
  command: "npm test",
};

describe("argChips", () => {
  it("picks out agent, model and effort by flag name, in chip order", () => {
    expect(argChips(CLAUDE.argv ?? [])).toEqual([
      { label: "Agent", value: "reviewer" },
      { label: "Model", value: "claude-haiku-5-5" },
      { label: "Effort", value: "high" },
    ]);
  });

  it("works on any program, reads --flag=value, and keeps a repeated flag's last value", () => {
    expect(argChips(["codex", "--model=o9", "--effort", "low", "--effort=max"])).toEqual([
      { label: "Model", value: "o9" },
      { label: "Effort", value: "max" },
    ]);
  });

  it("ignores the program, a flag with no value and the prompt", () => {
    expect(argChips(["--model"])).toEqual([]);
    expect(argChips(["x", "--model"])).toEqual([]);
    expect(argChips(["x", "--agent", "--model text"], 2)).toEqual([]);
    expect(argChips(["x", "-p", "--model=y"], 2)).toEqual([]);
  });
});

describe("promptIndex", () => {
  it("is null when the prompt is the stdin, and for shell stages", () => {
    expect(promptIndex(CLAUDE)).toBeNull();
    expect(promptIndex(SHELL)).toBeNull();
  });

  it("finds an argument prompt: the last element, or an earlier multi-line one", () => {
    expect(promptIndex(COPILOT)).toBe(5);
    expect(promptIndex({ ...COPILOT, argv: ["copilot", "-p", "fix it"] })).toBe(2);
    expect(promptIndex({ ...COPILOT, argv: ["x", PROMPT, "--quiet"] })).toBe(1);
  });

  it("finds none when the last element is a flag or there is only the program", () => {
    expect(promptIndex({ ...COPILOT, argv: ["x", "--quiet"] })).toBeNull();
    expect(promptIndex({ ...COPILOT, argv: ["x"] })).toBeNull();
  });
});

describe("command words", () => {
  it("quotes only what a shell would need quoted", () => {
    expect(shellWord("--output-format")).toBe("--output-format");
    expect(shellWord("a b")).toBe("'a b'");
    expect(shellWord("it's")).toBe("'it'\\''s'");
    expect(shellWord("")).toBe("''");
  });

  it("marks the program, flags and arguments, and hides the prompt", () => {
    expect(commandTokens(["copilot", "--model", "gpt 5", "-p", PROMPT], 4)).toEqual([
      { text: "copilot", kind: "program" },
      { text: "--model", kind: "flag" },
      { text: "'gpt 5'", kind: "arg" },
      { text: "-p", kind: "flag" },
      { text: PROMPT_PLACEHOLDER, kind: "prompt" },
    ]);
  });
});

describe("stageCall", () => {
  it("shows a stdin prompt beside the full command", () => {
    const call = stageCall(CLAUDE);
    expect(call.prompt).toEqual({ text: PROMPT, via: "stdin" });
    expect(call.tokens?.map((t) => t.text)).toEqual(CLAUDE.argv);
    expect(call.chips.map((c) => c.label)).toEqual(["Agent", "Model", "Effort"]);
    expect(call.command).toBeNull();
  });

  it("lifts an argument prompt out of the command", () => {
    const call = stageCall(COPILOT);
    expect(call.prompt).toEqual({ text: PROMPT, via: "arg" });
    expect(call.tokens?.at(-1)).toEqual({ text: PROMPT_PLACEHOLDER, kind: "prompt" });
    expect(call.chips).toEqual([{ label: "Model", value: "gpt-5" }]);
  });

  it("shows a shell stage's command", () => {
    expect(stageCall(SHELL)).toMatchObject({
      kind: "shell",
      command: "npm test",
      tokens: null,
      prompt: null,
      chips: [],
    });
  });
});

describe("loop stages", () => {
  const inv = (stdin: string | null, argv: string[], at: number | null, tail: string) => ({
    argv,
    stdin,
    prompt_arg: at,
    tail,
  });

  it("lists later rounds from round 2, then one repair per round", () => {
    const calls = laterCalls({
      rounds: [
        inv("R2", ["claude", "--resume", "s1"], null, "!"),
        inv("R3", ["claude"], null, "!"),
      ],
      repairs: [inv("Fix", ["claude"], null, "?")],
    });
    expect(calls.map((c) => [c.kind, c.round])).toEqual([
      ["round", 2],
      ["round", 3],
      ["repair", 1],
    ]);
    expect(new Set(calls.map((c) => c.key)).size).toBe(3);
    expect(calls[0]?.tokens.map((t) => t.text)).toEqual(["claude", "--resume", "s1"]);
  });

  it("shows where the worker appends its text: after the prompt, before the tail", () => {
    const [round, repair] = laterCalls({
      rounds: [inv("Again.", ["claude"], null, " END")],
      repairs: [inv(null, ["copilot", "-p", "Redo."], 2, " END")],
    });
    expect(round?.prompt).toBe(`Again.${FEEDBACK_PLACEHOLDER} END`);
    expect(repair?.prompt).toBe(`Redo.${ERROR_PLACEHOLDER} END`);
    expect(repair?.tokens.at(-1)?.kind).toBe("prompt");
  });

  it("labels the fold-out with what it holds", () => {
    const round = { kind: "round" as const };
    const repair = { kind: "repair" as const };
    expect(laterLabel([round, round])).toContain("2");
    expect(laterLabel([round, round])).not.toMatch(/repair/i);
    expect(laterLabel([repair])).toMatch(/repair/i);
    expect(laterLabel([round, round, repair, repair, repair])).toMatch(/2.*3/);
  });

  it("has no later calls outside a loop", () => {
    expect(laterCalls(CLAUDE)).toEqual([]);
    expect(stageCall(SHELL).later).toEqual([]);
  });

  it("describes how a check decides", () => {
    expect(checkMeta(CLAUDE)).toEqual([]);
    expect(checkMeta({ ...SHELL, check: true })).toHaveLength(2);
    const agent = { ...CLAUDE, check: true, verdict_from: "file" as const };
    expect(checkMeta({ ...agent, verdict_repairs: 2 })[2]).toContain("2");
    expect(checkMeta({ ...agent, verdict_repairs: 0 })).not.toEqual(
      checkMeta({ ...agent, verdict_repairs: 1 }),
    );
  });
});

describe("the rest", () => {
  it("lists file paths only, sorted", () => {
    const file = (path: string): Manifest["notes"][number] => ({
      path,
      name: path,
      content: "x".repeat(10),
    });
    expect(
      filePaths({
        context: [],
        notes: [file("TASKS.md"), file(".claude/agents/reviewer.md"), file("CONTEXT.md")],
      }),
    ).toEqual([".claude/agents/reviewer.md", "CONTEXT.md", "TASKS.md"]);
  });

  it("writes a time limit", () => {
    expect(timeoutText(45)).toBe("45s");
    expect(timeoutText(1800)).toBe("30m");
    expect(timeoutText(7200)).toBe("2h");
    expect(timeoutText(5400)).toBe("1h 30m");
  });

  it("says a queued run's manifest is built later, and shows other errors", () => {
    expect(manifestErrorText(new ApiError(404, "not_found", "no manifest"))).toEqual({
      text: "Built when a worker picks the run up",
      quiet: true,
    });
    expect(manifestErrorText(new ApiError(500, "http_error", "Boom"))).toEqual({
      text: "Can't load how the stages were called · Boom",
      quiet: false,
    });
  });
});
