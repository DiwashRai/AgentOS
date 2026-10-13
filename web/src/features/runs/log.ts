// Turn a run's events into display lines: raw output, summarised Claude stream-json, stage
// separators and warnings. Pure; the component only maps lines to elements.
import type { RunEvent } from "../../api/queries";
import { roundKey } from "./rounds";
import { summariseStreamJson, type SummaryKind } from "./streamJson";

export type LineKind =
  | "stdout"
  | "stderr"
  | "system"
  | "warning"
  | "stageStart"
  | "stageEnd"
  | "stageFail"
  | "stageDone"
  | "verdictPass"
  | "verdictFail"
  | "notes"
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

/** A stage's name in the log, with its round once a loop goes round again ("Review (round 2)"). */
function named(name: string, round: number): string {
  return round > 1 ? `${name} (round ${String(round)})` : name;
}

/** Every display line for `events`, in order. `stageName` maps a stage id to its name. */
export function buildLines(
  events: readonly RunEvent[],
  stageName: (id: string | null) => string,
): LogLine[] {
  const out: LogLine[] = [];
  /** Checks that said "fail", by `roundKey` (the verdict comes before its stage_finished). */
  const failing = new Set<string>();
  for (const e of events) {
    const key = String(e.seq);
    switch (e.type) {
      case "log":
        out.push(...logLines(e));
        break;
      case "warning":
        out.push({ key, kind: "warning", text: e.data.message, truncated: false, ts: e.ts });
        break;
      case "verdict_repair":
        out.push({
          key,
          kind: "warning",
          text: `${named(stageName(e.stage_id), e.data.round)} · invalid verdict, repair ${String(e.data.attempt)} · ${e.data.error}`,
          truncated: false,
          ts: e.ts,
        });
        break;
      case "verdict": {
        const pass = e.data.verdict === "pass";
        if (!pass && e.stage_id !== null) failing.add(roundKey(e.stage_id, e.data.round));
        out.push({
          key,
          kind: pass ? "verdictPass" : "verdictFail",
          text: `${named(stageName(e.stage_id), e.data.round)} · verdict ${e.data.verdict}`,
          truncated: false,
          ts: e.ts,
        });
        if (e.data.notes !== "") {
          out.push({
            key: `${key}.notes`,
            kind: "notes",
            text: clip(e.data.notes),
            truncated: false,
            ts: e.ts,
          });
        }
        break;
      }
      case "stage_started":
        out.push({
          key,
          kind: "stageStart",
          text: `${named(stageName(e.stage_id), e.data.round)} started`,
          truncated: false,
          ts: e.ts,
        });
        break;
      case "stage_finished": {
        // A check that said "fail" with rounds left finishes `succeeded`: it ran fine, but it
        // didn't pass, so it reads "done" in the quiet colour (its verdict line says the rest).
        const failedCheck =
          e.data.status === "succeeded" &&
          e.stage_id !== null &&
          failing.has(roundKey(e.stage_id, e.data.round));
        const parts = [
          named(stageName(e.stage_id), e.data.round),
          failedCheck ? "done" : FINISHED_WORD[e.data.status],
        ];
        if (e.data.exit_code !== null) parts.push(`exit ${String(e.data.exit_code)}`);
        if (e.data.reason) parts.push(e.data.reason);
        out.push({
          key,
          kind: failedCheck
            ? "stageDone"
            : e.data.status === "succeeded"
              ? "stageEnd"
              : "stageFail",
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
