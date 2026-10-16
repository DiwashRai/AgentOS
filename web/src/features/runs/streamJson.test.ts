import { describe, expect, it } from "vitest";

import { formatCost, oneLine, summariseStreamJson } from "./streamJson";

// Shapes as `claude -p --output-format stream-json --verbose` prints them (one JSON per line).
const INIT = JSON.stringify({
  type: "system",
  subtype: "init",
  cwd: "/tmp/ws",
  session_id: "1f0c",
  tools: ["Read", "Edit", "Bash"],
  model: "claude-opus-4-1",
  permissionMode: "bypassPermissions",
});

const ASSISTANT = JSON.stringify({
  type: "assistant",
  message: {
    id: "msg_01",
    type: "message",
    role: "assistant",
    model: "claude-opus-4-1",
    content: [
      { type: "thinking", thinking: "hmm", signature: "x" },
      { type: "text", text: "I'll read the plan first.\n" },
      {
        type: "tool_use",
        id: "toolu_01",
        name: "Read",
        input: { file_path: "/tmp/ws/.claude/skills/context-aos-1/TASKS.md" },
      },
      { type: "tool_use", id: "toolu_02", name: "Bash", input: { command: "npm test\n--watch" } },
      { type: "tool_use", id: "toolu_03", name: "TodoWrite", input: { todos: [{}, {}, {}] } },
      { type: "tool_use", id: "toolu_04", name: "Mystery", input: {} },
    ],
    stop_reason: null,
    usage: { input_tokens: 10, output_tokens: 20 },
  },
  parent_tool_use_id: null,
  session_id: "1f0c",
});

const TOOL_RESULT_OK = JSON.stringify({
  type: "user",
  message: {
    role: "user",
    content: [{ tool_use_id: "toolu_01", type: "tool_result", content: "- [ ] step one" }],
  },
  session_id: "1f0c",
});

const TOOL_RESULT_ERR = JSON.stringify({
  type: "user",
  message: {
    role: "user",
    content: [
      {
        tool_use_id: "toolu_02",
        type: "tool_result",
        is_error: true,
        content: [{ type: "text", text: "Exit code 1\nFAIL src/x.test.ts" }],
      },
    ],
  },
});

const RESULT = JSON.stringify({
  type: "result",
  subtype: "success",
  is_error: false,
  duration_ms: 48123,
  num_turns: 7,
  result: "Plan written to TASKS.md.",
  session_id: "1f0c",
  total_cost_usd: 0.08413,
  usage: {},
});

const RESULT_ERR = JSON.stringify({
  type: "result",
  subtype: "error_max_turns",
  is_error: true,
  num_turns: 30,
  total_cost_usd: 1.5,
});

describe("summariseStreamJson", () => {
  it("system/init → session started with the model", () => {
    expect(summariseStreamJson(INIT)).toEqual([
      { kind: "session", text: "Session started · claude-opus-4-1" },
    ]);
  });

  it("assistant → its text and one line per tool use; thinking is left out", () => {
    expect(summariseStreamJson(ASSISTANT)).toEqual([
      { kind: "agent", text: "I'll read the plan first." },
      { kind: "tool", text: "Read · /tmp/ws/.claude/skills/context-aos-1/TASKS.md" },
      { kind: "tool", text: "Bash · npm test" },
      { kind: "tool", text: "TodoWrite · 3 todos" },
      { kind: "tool", text: "Mystery" },
    ]);
  });

  it("successful tool results are hidden; failed ones get a line", () => {
    expect(summariseStreamJson(TOOL_RESULT_OK)).toEqual([]);
    expect(summariseStreamJson(TOOL_RESULT_ERR)).toEqual([
      { kind: "toolError", text: "Tool error · Exit code 1" },
    ]);
  });

  it("result → text and cost; error subtypes are flagged", () => {
    expect(summariseStreamJson(RESULT)).toEqual([
      { kind: "result", text: "Result: Plan written to TASKS.md. · $0.0841" },
    ]);
    expect(summariseStreamJson(RESULT_ERR)).toEqual([
      { kind: "resultError", text: "Result: error_max_turns · $1.50" },
    ]);
  });

  it("a multi-line result keeps its first line (the agent's message is shown above)", () => {
    const line = JSON.stringify({
      type: "result",
      subtype: "success",
      result: "Opened PR.\n\n- a",
    });
    expect(summariseStreamJson(line)).toEqual([{ kind: "result", text: "Result: Opened PR." }]);
  });

  it("system bookkeeping subtypes are hidden", () => {
    expect(summariseStreamJson('{"type":"system","subtype":"thinking_tokens"}')).toEqual([]);
  });

  it("rate limit events are noise", () => {
    expect(summariseStreamJson('{"type":"rate_limit_event","rate_limit_info":{}}')).toEqual([]);
  });

  it("anything else is shown raw (null)", () => {
    expect(summariseStreamJson("npm test")).toBeNull();
    expect(summariseStreamJson('{"type":"assistant", "message": {')).toBeNull(); // cut line
    expect(summariseStreamJson('{"level":"info"}')).toBeNull(); // JSON without `type`
    expect(summariseStreamJson('{"type":"toString"}')).toBeNull(); // unknown type
    expect(summariseStreamJson("[1,2]")).toBeNull();
  });
});

// Copilot CLI 1.0 `--output-format json`: one `{"type": "<area>.<event>", "data": {...}}` per line
// (the sample run from backend/worker/worker_tests/test_worker_echo.py).
const COPILOT = [
  { type: "session.start", data: { sessionId: "x", selectedModel: "gpt-5" }, id: "1" },
  { type: "user.message", data: { content: "Fix the login bug" } },
  { type: "assistant.turn_start", data: { turnId: "0" } },
  { type: "assistant.message_delta", data: { deltaContent: "I" }, ephemeral: true },
  { type: "assistant.reasoning", data: { reasoningId: "r", content: "Glob first." } },
  {
    type: "assistant.message",
    data: {
      messageId: "m1",
      content: "",
      reasoningText: "Glob first.",
      toolRequests: [{ toolCallId: "c1", name: "glob", arguments: {} }],
    },
  },
  {
    type: "tool.execution_start",
    data: { toolCallId: "c1", toolName: "glob", arguments: { pattern: "**/*.py" } },
  },
  {
    type: "tool.execution_complete",
    data: { toolCallId: "c1", success: true, result: { content: "a.py\nb.py\nc.py" } },
  },
  {
    type: "tool.execution_start",
    data: { toolCallId: "c2", toolName: "bash", arguments: { command: "rm x" } },
  },
  {
    type: "tool.execution_complete",
    data: { toolCallId: "c2", success: false, error: { message: "permission denied" } },
  },
  { type: "assistant.message", data: { messageId: "m2", content: "All done." } },
  { type: "assistant.turn_end", data: { turnId: "0" } },
  { type: "session.shutdown", data: { totalPremiumRequests: 1 } },
];

describe("Copilot JSONL", () => {
  it("a run reads as the session, the agent's text, its tool calls, failures and the end", () => {
    expect(COPILOT.flatMap((e) => summariseStreamJson(JSON.stringify(e)) ?? [])).toEqual([
      { kind: "session", text: "Session started · gpt-5" },
      { kind: "tool", text: "glob · **/*.py" },
      { kind: "tool", text: "bash · rm x" },
      { kind: "toolError", text: "Tool error · permission denied" },
      { kind: "agent", text: "All done." },
      { kind: "result", text: "Result: done · 1 premium request" },
    ]);
  });

  const one = (line: unknown) => summariseStreamJson(JSON.stringify(line));

  it.each([
    ["no model", { type: "session.start", data: "odd" }, "session", "Session started"],
    [
      "report_intent names its intent",
      {
        type: "tool.execution_start",
        data: { toolName: "report_intent", arguments: { intent: "Exploring", description: "d" } },
      },
      "tool",
      "report_intent · Exploring",
    ],
    ["unnamed tool", { type: "tool.execution_start", data: {} }, "tool", "tool"],
    [
      "a string error",
      { type: "tool.execution_complete", data: { success: false, error: "nope\nmore" } },
      "toolError",
      "Tool error · nope",
    ],
    [
      "no error text",
      { type: "tool.execution_complete", data: { success: false } },
      "toolError",
      "Tool error",
    ],
    [
      "session error",
      { type: "session.error", data: { message: "boom" } },
      "resultError",
      "Error: boom",
    ],
    [
      "abort",
      { type: "abort", data: { reason: "user cancelled" } },
      "resultError",
      "Aborted: user cancelled",
    ],
    ["shutdown without a count", { type: "session.shutdown", data: {} }, "result", "Result: done"],
    [
      "plural requests",
      { type: "session.shutdown", data: { totalPremiumRequests: 2.5 } },
      "result",
      "Result: done · 2.5 premium requests",
    ],
  ])("%s", (_name, line, kind, text) => {
    expect(one(line)).toEqual([{ kind, text }]);
  });

  it.each([
    ["successful tool result", { type: "tool.execution_complete", data: { success: true } }],
    ["reasoning", { type: "assistant.reasoning", data: { content: "x" } }],
    ["intent", { type: "assistant.intent", data: { intent: "Exploring" } }],
    ["usage", { type: "assistant.usage", data: { outputTokens: 9 } }],
    ["progress", { type: "tool.execution_progress", data: {} }],
    ["an unknown ephemeral event", { type: "subagent.progress", ephemeral: true }],
    ["a tools-only message", { type: "assistant.message", data: { content: "  " } }],
  ])("%s is hidden", (_name, line) => {
    expect(one(line)).toEqual([]);
  });

  it("an unknown event that isn't ephemeral is shown raw", () => {
    expect(one({ type: "hook.start", data: {} })).toBeNull();
  });

  it("odd shapes don't throw", () => {
    expect(one({ type: "tool.execution_complete", data: "odd", success: false })).toEqual([]);
    expect(
      one({ type: "tool.execution_complete", data: { success: false, error: "\nx" } }),
    ).toEqual([{ kind: "toolError", text: "Tool error · x" }]);
  });

  it("ephemeral only hides unknown events: a known Claude line still reads", () => {
    expect(one({ type: "result", subtype: "success", result: "Done.", ephemeral: true })).toEqual([
      { kind: "result", text: "Result: Done." },
    ]);
  });

  describe("workflow.result", () => {
    const wf = (run: unknown, name: unknown = "fix-login") =>
      one({ type: "workflow.result", data: { name, run } });

    it("completed shows the run's result; error is flagged with why it failed", () => {
      expect(wf({ status: "completed", result: "Tests pass.\nmore" })).toEqual([
        { kind: "result", text: "Result: fix-login completed · Tests pass." },
      ]);
      expect(wf({ status: "error", result: "partial", error: { message: "API 401" } })).toEqual([
        { kind: "resultError", text: "Result: fix-login error · API 401" },
      ]);
      expect(wf({ status: "error", failure: "tool crashed" })).toEqual([
        { kind: "resultError", text: "Result: fix-login error · tool crashed" },
      ]);
    });

    it("a stop that isn't an error says why, without the error flag", () => {
      for (const status of ["halted", "paused", "cancelled"]) {
        expect(wf({ status, reason: "waiting for input" })).toEqual([
          { kind: "result", text: `Result: fix-login ${status} · waiting for input` },
        ]);
      }
    });

    it("blank fields count as missing, and the detail skips leading blank lines", () => {
      expect(wf({ status: "halted", result: " ", reason: "\nwaiting" })).toEqual([
        { kind: "result", text: "Result: fix-login halted · waiting" },
      ]);
      expect(wf({ status: "error", error: {}, failure: "", reason: "quota" })).toEqual([
        { kind: "resultError", text: "Result: fix-login error · quota" },
      ]);
    });

    it("a status it doesn't know is flagged, as a Claude result that isn't success is", () => {
      expect(wf({ status: "failed" })?.[0]?.kind).toBe("resultError");
    });

    it("missing parts fall back", () => {
      expect(one({ type: "workflow.result", data: "odd" })?.[0]?.kind).toBe("result");
      expect(wf(undefined, null)).toEqual([{ kind: "result", text: "Result: done" }]);
      expect(wf({ status: "completed" }, 3)).toEqual([
        { kind: "result", text: "Result: completed" },
      ]);
    });
  });
});

describe("helpers", () => {
  it("oneLine keeps the first line and cuts long text", () => {
    expect(oneLine("a\nb")).toBe("a");
    expect(oneLine("x".repeat(10), 5)).toBe("xxxx…");
  });

  it("formatCost", () => {
    expect(formatCost(0.0123456)).toBe("$0.0123");
    expect(formatCost(2)).toBe("$2.00");
  });
});
