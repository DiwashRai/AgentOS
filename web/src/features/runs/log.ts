// Turn a run's events into display lines: raw output, summarised Claude stream-json, stage
// separators and warnings. Pure; the component only maps lines to elements.
import type { RunEvent } from "../../api/queries";
import { summariseStreamJson, type SummaryKind } from "./streamJson";

export type LineKind =
  | "stdout"
  | "stderr"
  | "system"
  | "warning"
  | "stageStart"
  | "stageEnd"
  | "stageFail"
  | SummaryKind;

export interface LogLine {
  key: string;
  kind: LineKind;
  text: string;
  /** The worker cut the line (SPEC §6 limits). */
  truncated: boolean;
  ts: string;
}

/** Raw lines longer than this are cut in the DOM (a cut stream-json line can be 256 KiB). */
export const RAW_MAX = 4000;

function clip(text: string): string {
  if (text.length <= RAW_MAX) return text;
  return `${text.slice(0, RAW_MAX)} … (+${String(text.length - RAW_MAX)} chars)`;
}

type LogEvent = Extract<RunEvent, { type: "log" }>;

// Log lines never change once posted, so their (possibly expensive) parse is cached per event
// object. The query keeps the same objects across polls, so each line is parsed once.
const cache = new WeakMap<LogEvent, LogLine[]>();

function logLines(e: LogEvent): LogLine[] {
  const hit = cache.get(e);
  if (hit) return hit;
  const base = { ts: e.ts, truncated: e.data.truncated };
  const key = String(e.seq);
  let out: LogLine[];
  const summary = e.data.stream === "stdout" ? summariseStreamJson(e.data.text) : null;
  if (summary === null) {
    out = [{ ...base, key, kind: e.data.stream, text: clip(e.data.text) }];
  } else {
    // A summary is readable on its own; the cut flag only matters for raw text.
    out = summary.map((s, i) => ({ ...base, truncated: false, key: `${key}.${String(i)}`, ...s }));
  }
  cache.set(e, out);
  return out;
}

const FINISHED_WORD = { succeeded: "passed", failed: "failed", cancelled: "cancelled" } as const;

/** Every display line for `events`, in order. `stageName` maps a stage id to its name. */
export function buildLines(
  events: readonly RunEvent[],
  stageName: (id: string | null) => string,
): LogLine[] {
  const out: LogLine[] = [];
  for (const e of events) {
    const key = String(e.seq);
    switch (e.type) {
      case "log":
        out.push(...logLines(e));
        break;
      case "warning":
        out.push({ key, kind: "warning", text: e.data.message, truncated: false, ts: e.ts });
        break;
      case "stage_started":
        out.push({
          key,
          kind: "stageStart",
          text: `${stageName(e.stage_id)} started`,
          truncated: false,
          ts: e.ts,
        });
        break;
      case "stage_finished": {
        const parts = [stageName(e.stage_id), FINISHED_WORD[e.data.status]];
        if (e.data.exit_code !== null) parts.push(`exit ${String(e.data.exit_code)}`);
        if (e.data.reason) parts.push(e.data.reason);
        out.push({
          key,
          kind: e.data.status === "succeeded" ? "stageEnd" : "stageFail",
          text: parts.join(" · "),
          truncated: false,
          ts: e.ts,
        });
        break;
      }
    }
  }
  return out;
}
