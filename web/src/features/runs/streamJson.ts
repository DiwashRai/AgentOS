// Summarise agent CLI stdout lines for the run log: Claude `--output-format stream-json` and
// Copilot `--output-format json` (JSONL, `{"type": "<area>.<event>", "data": {...}}`). The worker
// forwards each stdout line verbatim; dumping these JSON blobs would bury the agent's work, so
// each known line type becomes a few readable entries. The worker's console echo (`Echo` in
// worker.py) picks the same events. Pure: no React, no server calls.

export type SummaryKind = "agent" | "tool" | "toolError" | "session" | "result" | "resultError";

export interface Summary {
  kind: SummaryKind;
  text: string;
}

/** Longest tool-input detail or tool-error excerpt shown on one line. */
const DETAIL_MAX = 140;

/** The tool input fields worth naming, most telling first. */
const DETAIL_KEYS = [
  "file_path",
  "path",
  "notebook_path",
  "command",
  "pattern",
  "url",
  "query",
  "skill",
  "description",
  "prompt",
] as const;

/** Copilot's keys add its report_intent tool's `intent`, ahead of a description (as the worker's
 *  echo has them); Claude's stay as they were. */
const COPILOT_DETAIL_KEYS = [
  "file_path",
  "path",
  "notebook_path",
  "command",
  "pattern",
  "url",
  "query",
  "skill",
  "intent",
  "description",
  "prompt",
] as const;

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}

/** First line, cut to `max` characters with an ellipsis. */
export function oneLine(text: string, max = DETAIL_MAX): string {
  const first = (text.split(/\r?\n/, 1)[0] ?? "").trim();
  return first.length > max ? `${first.slice(0, max - 1)}…` : first;
}

function toolDetail(input: unknown, keys: readonly string[] = DETAIL_KEYS): string {
  if (!isObj(input)) return "";
  for (const k of keys) {
    const v = str(input[k]);
    if (v?.trim()) return oneLine(v);
  }
  if (Array.isArray(input.todos)) return `${String(input.todos.length)} todos`;
  return "";
}

function contentBlocks(message: unknown): Obj[] {
  if (!isObj(message) || !Array.isArray(message.content)) return [];
  return message.content.filter(isObj);
}

function assistant(line: Obj): Summary[] {
  const out: Summary[] = [];
  for (const block of contentBlocks(line.message)) {
    if (block.type === "text") {
      const text = str(block.text)?.trim();
      if (text) out.push({ kind: "agent", text });
    } else if (block.type === "tool_use") {
      const name = str(block.name) ?? "tool";
      const detail = toolDetail(block.input);
      out.push({ kind: "tool", text: detail ? `${name} · ${detail}` : name });
    }
    // `thinking` and unknown blocks are left out.
  }
  return out;
}

function toolResultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (isObj(part) && part.type === "text") {
        const t = str(part.text);
        if (t) return t;
      }
    }
  }
  return "";
}

/** Tool results are the bulk of the stream; only failures are worth a line. */
function user(line: Obj): Summary[] {
  return contentBlocks(line.message)
    .filter((b) => b.type === "tool_result" && b.is_error === true)
    .map((b) => {
      const text = oneLine(toolResultText(b.content));
      return { kind: "toolError", text: text ? `Tool error · ${text}` : "Tool error" };
    });
}

/** `$0.0123` below a dollar, `$1.24` above. */
export function formatCost(usd: number): string {
  return `$${usd.toFixed(usd < 1 ? 4 : 2)}`;
}

function result(line: Obj): Summary[] {
  const subtype = str(line.subtype) ?? "";
  const failed = line.is_error === true || (subtype !== "" && subtype !== "success");
  // The result text repeats the agent's last message (already shown above): one line is enough.
  const full = str(line.result)?.trim();
  const body = full ? oneLine(full) : subtype || "done";
  const cost =
    typeof line.total_cost_usd === "number" ? ` · ${formatCost(line.total_cost_usd)}` : "";
  return [{ kind: failed ? "resultError" : "result", text: `Result: ${body}${cost}` }];
}

function system(line: Obj): Summary[] {
  const subtype = str(line.subtype) ?? "";
  if (subtype === "init") {
    const model = str(line.model);
    return [{ kind: "session", text: model ? `Session started · ${model}` : "Session started" }];
  }
  // Other subtypes (thinking_tokens, task_started, compact_boundary…) are bookkeeping.
  return [];
}

// ------------------------------------------------------------------ Copilot JSONL

function data(line: Obj): Obj {
  return isObj(line.data) ? line.data : {};
}

/** A string field with something in it (trimmed); blank counts as missing. */
function filled(v: unknown): string | undefined {
  const text = str(v)?.trim();
  return text === "" ? undefined : text;
}

/** A label, plus the first line of `text` when there is one ("Error: boom"). */
function labelled(label: string, text: string | undefined): string {
  return text ? `${label}: ${oneLine(text)}` : label;
}

/** An error given as a string or as `{ message }`. */
function errorText(v: unknown): string | undefined {
  return filled(v) ?? (isObj(v) ? filled(v.message) : undefined);
}

function copilotStart(line: Obj): Summary[] {
  const model = str(data(line).selectedModel);
  return [{ kind: "session", text: model ? `Session started · ${model}` : "Session started" }];
}

/** The agent's text. Its reasoning is left out, as Claude's thinking is; a message that only
 *  requests tools is empty (each call has its own execution_start). */
function copilotMessage(line: Obj): Summary[] {
  const text = filled(data(line).content);
  return text ? [{ kind: "agent", text }] : [];
}

function copilotToolStart(line: Obj): Summary[] {
  const d = data(line);
  const name = str(d.toolName) ?? "tool";
  const detail = toolDetail(d.arguments, COPILOT_DETAIL_KEYS);
  return [{ kind: "tool", text: detail ? `${name} · ${detail}` : name }];
}

/** As with Claude, only a failed tool call is worth a line. */
function copilotToolDone(line: Obj): Summary[] {
  const d = data(line);
  if (d.success !== false) return [];
  const error = errorText(d.error);
  return [{ kind: "toolError", text: error ? `Tool error · ${oneLine(error)}` : "Tool error" }];
}

function copilotShutdown(line: Obj): Summary[] {
  const n = data(line).totalPremiumRequests;
  const requests =
    typeof n === "number" ? ` · ${String(n)} premium request${n === 1 ? "" : "s"}` : "";
  return [{ kind: "result", text: `Result: done${requests}` }];
}

/** A workflow run's statuses that aren't a failure; any other (`error`, or one we don't know)
 *  is flagged, as a Claude result that isn't `success` is. */
const WORKFLOW_OK = new Set(["completed", "halted", "paused", "cancelled"]);

/** The workflow's closing record: the detail is the run's result, or why it stopped (for a
 *  failure, why it failed first). */
function workflowResult(line: Obj): Summary[] {
  const d = data(line);
  const run = isObj(d.run) ? d.run : {};
  const status = filled(run.status);
  const failed = status !== undefined && !WORKFLOW_OK.has(status);
  const why = errorText(run.error) ?? errorText(run.failure) ?? filled(run.reason);
  const result = filled(run.result);
  const detail = failed ? (why ?? result) : (result ?? why);
  const name = filled(d.name);
  const head = [name, status ?? "done"].filter(Boolean).join(" ");
  const body = detail ? `${head} · ${oneLine(detail)}` : head;
  return [{ kind: failed ? "resultError" : "result", text: `Result: ${body}` }];
}

/** Copilot events with nothing worth a line (as the worker's echo has them): prompts, turn and
 *  usage bookkeeping, streamed deltas and progress, reasoning, and the agent's intent label. */
const COPILOT_QUIET = [
  "user.message",
  "assistant.turn_start",
  "assistant.turn_end",
  "assistant.usage",
  "assistant.message_delta",
  "assistant.reasoning_delta",
  "assistant.reasoning",
  "assistant.intent",
  "tool.execution_partial_result",
  "tool.execution_progress",
  "session.usage_info",
];

const HANDLERS = new Map<string, (line: Obj) => Summary[]>([
  // Claude stream-json
  ["assistant", assistant],
  ["user", user],
  ["result", result],
  ["system", system],
  // Noise for a human reading the log.
  ["rate_limit_event", () => []],
  ["stream_event", () => []],
  // Copilot JSONL
  ["session.start", copilotStart],
  ["assistant.message", copilotMessage],
  ["tool.execution_start", copilotToolStart],
  ["tool.execution_complete", copilotToolDone],
  [
    "session.error",
    (line) => [{ kind: "resultError", text: labelled("Error", filled(data(line).message)) }],
  ],
  [
    "abort",
    (line) => [{ kind: "resultError", text: labelled("Aborted", filled(data(line).reason)) }],
  ],
  ["session.shutdown", copilotShutdown],
  ["workflow.result", workflowResult],
  ...COPILOT_QUIET.map((type) => [type, () => []] as const),
]);

/**
 * Summarise one stdout line. `null` = not a known Claude or Copilot line (show it raw);
 * `[]` = a known line with nothing worth showing. A Copilot event marked `ephemeral` (streamed
 * progress) is never shown.
 */
export function summariseStreamJson(line: string): Summary[] | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("{")) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!isObj(parsed)) return null;
  const type = str(parsed.type);
  const handler = type === undefined ? undefined : HANDLERS.get(type);
  if (handler) return handler(parsed);
  return type !== undefined && parsed.ephemeral === true ? [] : null;
}
