// Summarise Claude CLI `--output-format stream-json` stdout lines for the run log. The worker
// forwards each stdout line verbatim; dumping these JSON blobs would bury the agent's work, so
// each known line type becomes a few readable entries. Pure: no React, no server calls.

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

function toolDetail(input: unknown): string {
  if (!isObj(input)) return "";
  for (const k of DETAIL_KEYS) {
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

const HANDLERS = new Map<string, (line: Obj) => Summary[]>([
  ["assistant", assistant],
  ["user", user],
  ["result", result],
  ["system", system],
  // Noise for a human reading the log.
  ["rate_limit_event", () => []],
  ["stream_event", () => []],
]);

/**
 * Summarise one stdout line. `null` = not a known stream-json line (show it raw);
 * `[]` = a known line with nothing worth showing.
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
  return handler ? handler(parsed) : null;
}
