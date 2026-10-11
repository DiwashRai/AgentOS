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
