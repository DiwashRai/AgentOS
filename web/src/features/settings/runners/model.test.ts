import { describe, expect, it } from "vitest";

import {
  addRuntime,
  argsToLines,
  commandPreview,
  fixFirst,
  linesToArgs,
  moveRuntime,
  patchRuntime,
  placeholderNotes,
  previewLine,
  removeRuntime,
  rowsFrom,
  runnerBody,
  runnerForm,
  runtimesBody,
  runtimesIssueCount,
  sameRunner,
  sameRuntimes,
  setPromptVia,
  shownArg,
  validateRunner,
  validateRuntimes,
  type RunnerSettings,
  type RuntimeEntry,
} from "./model";

const CLAUDE: RuntimeEntry = {
  name: "claude",
  argv: ["claude", "-p", "--output-format", "stream-json"],
  model_args: ["--model", "{model}"],
  agent_args: ["--agent", "{agent}"],
  prompt_via: "stdin",
  prompt_args: [],
};
const COPILOT: RuntimeEntry = {
  name: "copilot",
  argv: ["copilot", "--allow-all"],
  model_args: ["--model", "{model}"],
  agent_args: ["--agent", "{agent}"],
  prompt_via: "arg",
  prompt_args: ["-p", "{prompt}"],
};

const RUNNER: RunnerSettings = {
  launcher: "github",
  github_repo: "DiwashRai/AgentOS",
  workflow: "agentos-worker.yml",
  ref: "main",
  token_set: true,
  version: 2,
  updated_at: "2026-10-12T09:00:00Z",
  updated_by: null,
};

describe("runner form", () => {
  it("round-trips the server copy; an empty repo is sent as null and fields are trimmed", () => {
    const form = runnerForm({ ...RUNNER, github_repo: null });
    expect(form.github_repo).toBe("");
    expect(runnerBody({ ...form, workflow: " w.yaml ", ref: " dev " }, 4)).toEqual({
      github_repo: null,
      workflow: "w.yaml",
      ref: "dev",
      base_version: 4,
    });
    expect(runnerBody(runnerForm(RUNNER), 2).github_repo).toBe("DiwashRai/AgentOS");
  });

  it("compares as typed", () => {
    const form = runnerForm(RUNNER);
    expect(sameRunner(form, { ...form })).toBe(true);
    expect(sameRunner(form, { ...form, ref: "main " })).toBe(false);
  });

  it("mirrors the server's patterns", () => {
    const ok = runnerForm(RUNNER);
    expect(validateRunner(ok)).toEqual({});
    expect(validateRunner({ ...ok, github_repo: "" })).toEqual({});
    expect(validateRunner({ ...ok, github_repo: "AgentOS" }).github_repo).toBeDefined();
    expect(validateRunner({ ...ok, github_repo: "a/b/c" }).github_repo).toBeDefined();
    expect(validateRunner({ ...ok, workflow: "worker.json" }).workflow).toBeDefined();
    expect(validateRunner({ ...ok, workflow: ".github/workflows/w.yml" }).workflow).toBeDefined();
    expect(validateRunner({ ...ok, workflow: "w.yaml" })).toEqual({});
    expect(validateRunner({ ...ok, ref: "  " }).ref).toBeDefined();
    expect(validateRunner({ ...ok, ref: "x".repeat(256) }).ref).toBeDefined();
  });
});

describe("lines and arguments", () => {
  it("is one argument per line, ignoring blank lines and keeping lines as typed", () => {
    expect(linesToArgs("claude\n-p\n\n  \n--model \n")).toEqual(["claude", "-p", "--model "]);
    expect(linesToArgs("")).toEqual([]);
    expect(argsToLines(["a", "b c"])).toBe("a\nb c");
  });
});

describe("runtime rows", () => {
  const rows = rowsFrom([CLAUDE, COPILOT]);

  it("keys saved rows by name and strips keys from the body", () => {
    expect(rows.map((r) => r.key)).toEqual(["s:claude", "s:copilot"]);
    expect(runtimesBody(rows, 3)).toEqual({ runtimes: [CLAUDE, COPILOT], base_version: 3 });
  });

  it("adds an empty draft with a fresh name and key", () => {
    const one = addRuntime(rows);
    const two = addRuntime(one);
    expect(one[2]?.key).toBe("n:1");
    expect(one[2]?.entry).toEqual({
      name: "runtime",
      argv: [],
      model_args: [],
      agent_args: [],
      prompt_via: "stdin",
      prompt_args: [],
    });
    expect(two[3]?.key).toBe("n:2");
    expect(two[3]?.entry.name).toBe("runtime-2");
  });

  it("moves (the first is the default) and removes, keeping at least one", () => {
    const moved = moveRuntime(rows, "s:copilot", -1);
    expect(moved.map((r) => r.entry.name)).toEqual(["copilot", "claude"]);
    expect(moveRuntime(rows, "s:claude", -1)).toEqual(rows);
    expect(moveRuntime(rows, "s:copilot", 1)).toEqual(rows);
    const removed = removeRuntime(rows, "s:claude");
    expect(removed.map((r) => r.entry.name)).toEqual(["copilot"]);
    expect(removeRuntime(removed, "s:copilot")).toEqual(removed);
  });

  it("compares without keys, order included", () => {
    expect(sameRuntimes(rows, rowsFrom([CLAUDE, COPILOT]))).toBe(true);
    const rekeyed = rows.map((r, i) => ({ ...r, key: `n:${String(i)}` }));
    expect(sameRuntimes(rows, rekeyed)).toBe(true);
    expect(sameRuntimes(rows, moveRuntime(rows, "s:copilot", -1))).toBe(false);
    expect(sameRuntimes(rows, patchRuntime(rows, "s:claude", { argv: ["claude"] }))).toBe(false);
  });

  it("switching to stdin clears the prompt arguments; to arg starts with {prompt}", () => {
    const stdin = setPromptVia(rows, "s:copilot", "stdin");
    expect(stdin[1]?.entry).toMatchObject({ prompt_via: "stdin", prompt_args: [] });
    const arg = setPromptVia(stdin, "s:copilot", "arg");
    expect(arg[1]?.entry).toMatchObject({ prompt_via: "arg", prompt_args: ["{prompt}"] });
    expect(setPromptVia(rows, "s:copilot", "arg")).toEqual(rows);
  });
});

describe("validateRuntimes", () => {
  const check = (...entries: RuntimeEntry[]) => validateRuntimes(rowsFrom(entries));

  it("accepts the built-in runtimes", () => {
    expect(runtimesIssueCount(check(CLAUDE, COPILOT))).toBe(0);
  });

  it("checks names: pattern and unique", () => {
    expect(check({ ...CLAUDE, name: "Claude" }).rows[0]?.name).toBeDefined();
    expect(check({ ...CLAUDE, name: "-x" }).rows[0]?.name).toBeDefined();
    expect(check({ ...CLAUDE, name: "a".repeat(33) }).rows[0]?.name).toBeDefined();
    expect(check({ ...CLAUDE, name: "9.x_y-z" }).rows[0]).toEqual({});
    const dup = check(CLAUDE, { ...COPILOT, name: "claude" });
    expect(dup.rows[0]?.name).toBeDefined();
    expect(dup.rows[1]?.name).toBeDefined();
  });

  it("needs a program first", () => {
    expect(check({ ...CLAUDE, argv: [] }).rows[0]?.argv).toBeDefined();
    expect(check({ ...CLAUDE, argv: [" ", "-p"] }).rows[0]?.argv).toBeDefined();
  });

  it("limits argument counts and lengths, and rejects NUL", () => {
    const many = Array.from({ length: 51 }, (_, i) => `a${String(i)}`);
    expect(check({ ...CLAUDE, model_args: many }).rows[0]?.model_args).toBeDefined();
    expect(check({ ...CLAUDE, agent_args: ["x".repeat(1001)] }).rows[0]?.agent_args).toBeDefined();
    expect(check({ ...CLAUDE, argv: ["claude", "a\0b"] }).rows[0]?.argv).toBeDefined();
  });

  it("prompt_via arg needs a whole {prompt} line; stdin takes no prompt arguments", () => {
    expect(check({ ...COPILOT, prompt_args: ["-p"] }).rows[0]?.prompt_args).toBeDefined();
    expect(check({ ...COPILOT, prompt_args: ["-p={prompt}"] }).rows[0]?.prompt_args).toBeDefined();
    expect(check({ ...CLAUDE, prompt_args: ["-p"] }).rows[0]?.prompt_args).toBeDefined();
  });

  it("needs at least one runtime and at most 20", () => {
    expect(validateRuntimes([]).form).toHaveLength(1);
    const many = Array.from({ length: 21 }, (_, i) => ({ ...CLAUDE, name: `r${String(i)}` }));
    expect(check(...many).form).toHaveLength(1);
  });

  it("says how many issues to fix", () => {
    expect(fixFirst(0)).toBeUndefined();
    expect(fixFirst(1)).toBe("Fix 1 issue first");
    expect(fixFirst(3)).toBe("Fix 3 issues first");
  });
});

describe("placeholderNotes", () => {
  it("is quiet for the built-ins", () => {
    expect(placeholderNotes(CLAUDE)).toEqual([]);
    expect(placeholderNotes(COPILOT)).toEqual([]);
  });

  it("flags a placeholder that isn't a whole line, or sits in another group", () => {
    const notes = placeholderNotes({
      ...CLAUDE,
      argv: ["claude", "{model}"],
      model_args: ["--model={model}"],
    });
    expect(notes).toEqual([
      "{model} in the command stays as typed: it is replaced only in model arguments.",
      '"--model={model}" stays as typed: {model} is replaced only when it is a whole line.',
      "Model arguments have no {model} line, so the model isn't passed.",
    ]);
  });
});

describe("commandPreview", () => {
  it("assembles argv, model, agent (prompt on stdin)", () => {
    const p = commandPreview(CLAUDE);
    expect(p.stdin).toBe(true);
    expect(previewLine(p)).toBe(
      "claude -p --output-format stream-json --model <model> --agent <agent>",
    );
    expect(p.tokens.filter((t) => t.slot).map((t) => t.text)).toEqual(["<model>", "<agent>"]);
  });

  it("puts the prompt arguments last when the prompt is an argument", () => {
    const p = commandPreview(COPILOT);
    expect(p.stdin).toBe(false);
    expect(previewLine(p)).toBe("copilot --allow-all --model <model> --agent <agent> -p <prompt>");
  });

  it("keeps placeholders that aren't whole lines (or in another group) literal", () => {
    const p = commandPreview({
      ...CLAUDE,
      argv: ["claude", "{agent}"],
      model_args: ["-m={model}"],
    });
    expect(previewLine(p)).toBe("claude {agent} -m={model} --agent <agent>");
  });

  it("quotes arguments with spaces or quotes, and empty ones", () => {
    expect(shownArg("a b")).toBe('"a b"');
    expect(shownArg("")).toBe('""');
    expect(shownArg('say "hi"')).toBe('"say \\"hi\\""');
    expect(shownArg("--x")).toBe("--x");
  });
});
