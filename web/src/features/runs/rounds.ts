// Loop rounds on the Runs tab (request 24): group a run's stage executions by round, and fold the
// `verdict` / `verdict_repair` events into each check's outcome. Pure; it only selects and groups
// server fields (`StageRunInfo.round` / `verdict`, the events), never decides a verdict or a state.
import type { Schemas } from "../../api/client";
import type { RunEvent } from "../../api/queries";
import { stageMark, type MarkInfo } from "./runFormat";

export type StageRun = Schemas["StageRunInfo"];
export type Verdict = NonNullable<StageRun["verdict"]>;
export type VerdictSource = Schemas["VerdictData"]["source"];

/** One row of the stage list: a stage outside any repeated loop, or one round of a loop block. */
export type StageItem =
  | { type: "stage"; run: StageRun }
  | { type: "round"; key: string; round: number; runs: StageRun[] };

/**
 * The stage list in execution order (`idx`), with the rows of every loop that went past round 1
 * grouped by round. A stage is a loop stage when it ran in a round after the first; a block that
 * passed in round 1 stays flat (nothing to tell apart). Consecutive loop rows of one round form one
 * group; a new round, or another block, starts the next.
 */
export function groupByRound(stageRuns: readonly StageRun[]): StageItem[] {
  const rows = [...stageRuns].sort((a, b) => a.idx - b.idx);
  const looped = new Set(rows.filter((r) => r.round > 1).map((r) => r.stage_id));
  const items: StageItem[] = [];
  for (const run of rows) {
    if (!looped.has(run.stage_id)) {
      items.push({ type: "stage", run });
      continue;
    }
    const last = items.at(-1);
    if (last?.type === "round" && last.round === run.round) {
      last.runs.push(run);
    } else {
      items.push({ type: "round", key: `r${String(run.idx)}`, round: run.round, runs: [run] });
    }
  }
  return items;
}

/** A round's checks by verdict, for its header ("1 check failed"). */
export function roundCounts(runs: readonly StageRun[]): { pass: number; fail: number } {
  return {
    pass: runs.filter((r) => r.verdict === "pass").length,
    fail: runs.filter((r) => r.verdict === "fail").length,
  };
}

/** A round header's meta: how its checks went so far ("1 failed · 1 passed"), "" before any. */
export function roundMeta(c: { pass: number; fail: number }): string {
  return [
    c.fail > 0 ? `${String(c.fail)} failed` : "",
    c.pass > 0 ? `${String(c.pass)} passed` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** A check's mark: its verdict once it has one (a "fail" is `succeeded` until the last round, which
 *  would otherwise draw as a green tick), else the stage's status. */
export function checkMark(run: Pick<StageRun, "status" | "verdict">): MarkInfo {
  if (run.verdict !== null && (run.status === "succeeded" || run.status === "failed")) {
    return VERDICT_MARK[run.verdict];
  }
  return stageMark(run.status);
}

const VERDICT_MARK: Record<Verdict, MarkInfo> = {
  pass: { kind: "done", word: "Pass" },
  fail: { kind: "blk", word: "Fail" },
};

/** The verdict chip's word. */
export function verdictWord(v: Verdict): string {
  return VERDICT_MARK[v].word;
}

/** What a check said in one round, from its events. */
export interface CheckOutcome {
  verdict: Verdict | null;
  notes: string;
  source: VerdictSource | null;
  /** Each repair asked for, with why the verdict was invalid. */
  repairs: { attempt: number; error: string }[];
}

/** The key of a stage execution: a stage runs at most once per round. */
export function roundKey(stageId: string, round: number): string {
  return `${stageId}#${String(round)}`;
}

/** Every check outcome in `events`, by `roundKey`. Events without a stage are ignored. */
export function foldChecks(events: readonly RunEvent[]): Map<string, CheckOutcome> {
  const out = new Map<string, CheckOutcome>();
  const at = (stageId: string, round: number): CheckOutcome => {
    const key = roundKey(stageId, round);
    let o = out.get(key);
    if (!o) {
      o = { verdict: null, notes: "", source: null, repairs: [] };
      out.set(key, o);
    }
    return o;
  };
  for (const e of events) {
    if (e.stage_id === null) continue;
    if (e.type === "verdict") {
      const o = at(e.stage_id, e.data.round);
      o.verdict = e.data.verdict;
      o.notes = e.data.notes;
      o.source = e.data.source;
    } else if (e.type === "verdict_repair") {
      at(e.stage_id, e.data.round).repairs.push({ attempt: e.data.attempt, error: e.data.error });
    }
  }
  return out;
}

const SOURCE_WORD: Record<VerdictSource, string> = {
  structured_output: "structured output",
  file: "verdict file",
  exit_code: "exit code",
};

/** How the verdict was read, as shown. */
export function verdictSourceWord(source: VerdictSource): string {
  return SOURCE_WORD[source];
}

/** A check's fold-out under its row: what it holds and its one-line summary, or null when there is
 *  nothing to unfold (a pass without notes and without repairs). */
export function checkFold(
  o: CheckOutcome | undefined,
): { label: "Notes" | "Repairs"; meta: string } | null {
  if (!o || (o.notes === "" && o.repairs.length === 0)) return null;
  const n = o.repairs.length;
  const meta = [
    o.source ? `via ${verdictSourceWord(o.source)}` : "",
    n > 0 ? `${String(n)} repair${n === 1 ? "" : "s"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return { label: o.notes === "" ? "Repairs" : "Notes", meta };
}
