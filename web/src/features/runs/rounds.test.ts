import { describe, expect, it } from "vitest";

import type { RunEvent } from "../../api/queries";
import {
  checkFold,
  checkMark,
  foldChecks,
  groupByRound,
  roundCounts,
  roundKey,
  type StageItem,
  type StageRun,
} from "./rounds";

const TS = "2026-10-13T10:00:00Z";

function sr(idx: number, stageId: string, round = 1, over: Partial<StageRun> = {}): StageRun {
  return {
    idx,
    stage_id: stageId,
    name: stageId,
    round,
    status: "succeeded",
    verdict: null,
    exit_code: 0,
    reason: null,
    started_at: null,
    finished_at: null,
    ...over,
  };
}

/** An item list as [type, round, ids] for easy comparison. */
const shape = (items: StageItem[]) =>
  items.map((it) =>
    it.type === "stage"
      ? ["stage", it.run.stage_id]
      : ["round", it.round, it.runs.map((r) => r.stage_id)],
  );

describe("groupByRound", () => {
  it("leaves a run without a second round flat, in idx order", () => {
    const items = groupByRound([sr(2, "review"), sr(0, "plan"), sr(1, "implement")]);
    expect(shape(items)).toEqual([
      ["stage", "plan"],
      ["stage", "implement"],
      ["stage", "review"],
    ]);
  });

  it("groups a repeated block by round, with the stages around it outside", () => {
    const items = groupByRound([
      sr(0, "plan"),
      sr(1, "implement"),
      sr(2, "review", 1, { verdict: "fail" }),
      sr(3, "implement", 2),
      sr(4, "review", 2, { verdict: "fail" }),
      sr(5, "implement", 3),
      sr(6, "review", 3, { verdict: "pass" }),
      sr(7, "pr"),
    ]);
    expect(shape(items)).toEqual([
      ["stage", "plan"],
      ["round", 1, ["implement", "review"]],
      ["round", 2, ["implement", "review"]],
      ["round", 3, ["implement", "review"]],
      ["stage", "pr"],
    ]);
    expect(new Set(items.map((it) => (it.type === "round" ? it.key : ""))).size).toBe(4);
  });

  it("starts a run dispatched from inside a block at its round 1 check", () => {
    const items = groupByRound([
      sr(0, "review", 1, { verdict: "fail" }),
      sr(1, "implement", 2),
      sr(2, "review", 2),
    ]);
    expect(shape(items)).toEqual([
      ["round", 1, ["review"]],
      ["round", 2, ["implement", "review"]],
    ]);
  });

  it("splits two blocks that follow each other", () => {
    const items = groupByRound([
      sr(0, "a"),
      sr(1, "b"),
      sr(2, "a", 2),
      sr(3, "b", 2),
      sr(4, "c"),
      sr(5, "d"),
      sr(6, "c", 2),
      sr(7, "d", 2),
    ]);
    expect(shape(items).map((x) => x[0] === "round" && x[1])).toEqual([1, 2, 1, 2]);
  });
});

describe("checks", () => {
  it("counts a round's verdicts", () => {
    expect(
      roundCounts([
        sr(0, "a"),
        sr(1, "b", 1, { verdict: "pass" }),
        sr(2, "c", 1, { verdict: "fail" }),
        sr(3, "d", 1, { verdict: "fail" }),
      ]),
    ).toEqual({ pass: 1, fail: 2 });
  });

  it("marks a check by its verdict once it finished, else by its status", () => {
    expect(checkMark({ status: "succeeded", verdict: "fail" }).kind).toBe("blk");
    expect(checkMark({ status: "failed", verdict: "fail" }).kind).toBe("blk");
    expect(checkMark({ status: "succeeded", verdict: "pass" }).kind).toBe("done");
    expect(checkMark({ status: "succeeded", verdict: null }).kind).toBe("done");
    expect(checkMark({ status: "running", verdict: null }).kind).toBe("run");
    // no valid verdict: the stage failed
    expect(checkMark({ status: "failed", verdict: null }).kind).toBe("blk");
    expect(checkMark({ status: "cancelled", verdict: "pass" }).kind).toBe("blk");
  });

  it("folds verdicts and repairs per stage and round, ignoring other events", () => {
    const at = { ts: TS, stage_id: "review" } as const;
    const events: RunEvent[] = [
      { ...at, seq: 1, type: "verdict_repair", data: { round: 1, attempt: 1, error: "e1" } },
      { ...at, seq: 2, type: "verdict_repair", data: { round: 1, attempt: 2, error: "e2" } },
      {
        ...at,
        seq: 3,
        type: "verdict",
        data: { round: 1, verdict: "fail", notes: "n1", source: "file", repairs: 2 },
      },
      { ...at, seq: 4, type: "verdict_repair", data: { round: 2, attempt: 1, error: "e3" } },
      { ...at, seq: 5, stage_id: null, type: "warning", data: { message: "w" } },
      { ...at, seq: 6, type: "stage_started", data: { round: 3 } },
    ];
    const checks = foldChecks(events);
    expect(checks.size).toBe(2);
    expect(checks.get(roundKey("review", 1))).toEqual({
      verdict: "fail",
      notes: "n1",
      source: "file",
      repairs: [
        { attempt: 1, error: "e1" },
        { attempt: 2, error: "e2" },
      ],
    });
    // no valid verdict after the repair: only the repair is known
    expect(checks.get(roundKey("review", 2))).toMatchObject({ verdict: null, repairs: [{}] });
  });

  it("unfolds notes, else repairs, else nothing", () => {
    const base = { verdict: "pass" as const, notes: "", source: "structured_output" as const };
    expect(checkFold(undefined)).toBeNull();
    expect(checkFold({ ...base, repairs: [] })).toBeNull();
    expect(checkFold({ ...base, notes: "x", repairs: [] })?.label).toBe("Notes");
    const repairsOnly = checkFold({ ...base, verdict: null, source: null, repairs: [r(1)] });
    expect(repairsOnly?.label).toBe("Repairs");
    expect(checkFold({ ...base, notes: "x", repairs: [r(1), r(2)] })?.meta).toContain("2");
  });
});

function r(attempt: number) {
  return { attempt, error: `e${String(attempt)}` };
}
