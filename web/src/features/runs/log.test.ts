import { describe, expect, it } from "vitest";

import type { RunEvent } from "../../api/queries";
import { buildLines, RAW_MAX } from "./log";

const TS = "2026-10-11T10:00:00Z";
const names = (id: string | null) => (id === "plan" ? "Plan" : (id ?? "Run"));

function log(seq: number, stream: "stdout" | "stderr" | "system", text: string, truncated = false) {
  return { seq, ts: TS, stage_id: "plan", type: "log", data: { stream, text, truncated } } as const;
}

describe("buildLines", () => {
  it("renders every event kind in order", () => {
    const events: RunEvent[] = [
      { seq: 1, ts: TS, stage_id: "plan", type: "stage_started", data: { round: 1 } },
      log(2, "system", "$ claude -p"),
      log(3, "stdout", JSON.stringify({ type: "system", subtype: "init" })),
      log(4, "stdout", "plain output"),
      log(5, "stderr", "oops", true),
      { seq: 6, ts: TS, stage_id: "plan", type: "warning", data: { message: "dropped logs" } },
      {
        seq: 7,
        ts: TS,
        stage_id: "plan",
        type: "stage_finished",
        data: { status: "failed", exit_code: 1, reason: "timeout", round: 1 },
      },
    ];
    const lines = buildLines(events, names);
    expect(lines.map((l) => [l.kind, l.text, l.truncated])).toEqual([
      ["stageStart", "Plan started", false],
      ["system", "$ claude -p", false],
      ["session", "Session started", false],
      ["stdout", "plain output", false],
      ["stderr", "oops", true],
      ["warning", "dropped logs", false],
      ["stageFail", "Plan · failed · exit 1 · timeout", false],
    ]);
    expect(new Set(lines.map((l) => l.key)).size).toBe(lines.length);
  });

  it("splits one assistant line into several keyed lines", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: {
        content: [
          { type: "text", text: "Done." },
          { type: "tool_use", name: "Edit", input: { file_path: "a.ts" } },
        ],
      },
    });
    const lines = buildLines([log(9, "stdout", line)], names);
    expect(lines.map((l) => [l.key, l.kind])).toEqual([
      ["9.0", "agent"],
      ["9.1", "tool"],
    ]);
  });

  it("a passed stage reads passed", () => {
    const lines = buildLines(
      [
        {
          seq: 1,
          ts: TS,
          stage_id: "plan",
          type: "stage_finished",
          data: { status: "succeeded", exit_code: 0, reason: null, round: 1 },
        },
      ],
      names,
    );
    expect(lines[0]).toMatchObject({ kind: "stageEnd", text: "Plan · passed · exit 0" });
  });

  it("names the round once a loop goes round again, and folds in verdicts and repairs", () => {
    const names2 = (id: string | null) => (id === "review" ? "Review" : (id ?? "Run"));
    const at = { ts: TS, stage_id: "review" } as const;
    const events: RunEvent[] = [
      { ...at, seq: 1, type: "stage_started", data: { round: 1 } },
      { ...at, seq: 2, type: "verdict_repair", data: { round: 1, attempt: 1, error: "bad json" } },
      {
        ...at,
        seq: 3,
        type: "verdict",
        data: { round: 1, verdict: "fail", notes: "Fix a.ts", source: "file", repairs: 1 },
      },
      {
        ...at,
        seq: 4,
        type: "stage_finished",
        data: {
          status: "succeeded",
          exit_code: 0,
          reason: "verdict: fail (round 1 of 3)",
          round: 1,
        },
      },
      { ...at, seq: 5, type: "stage_started", data: { round: 2 } },
      {
        ...at,
        seq: 6,
        type: "verdict",
        data: { round: 2, verdict: "pass", notes: "", source: "structured_output", repairs: 0 },
      },
      {
        ...at,
        seq: 7,
        type: "stage_finished",
        data: { status: "succeeded", exit_code: 0, reason: null, round: 2 },
      },
    ];
    const lines = buildLines(events, names2);
    expect(lines.map((l) => l.kind)).toEqual([
      "stageStart",
      "warning", // the repair
      "verdictFail",
      "notes",
      "stageDone", // a failing check finished: not "passed"
      "stageStart",
      "verdictPass", // a pass without notes adds no notes line
      "stageEnd",
    ]);
    expect(lines[0]?.text).not.toContain("round");
    expect(lines[5]?.text).toContain("round 2");
    expect(lines[3]?.text).toBe("Fix a.ts");
    expect(lines[1]?.text).toContain("bad json");
    expect(new Set(lines.map((l) => l.key)).size).toBe(lines.length);
  });

  it("clips very long raw lines", () => {
    const [line] = buildLines([log(1, "stdout", "x".repeat(RAW_MAX + 10))], names);
    expect(line?.text.endsWith("… (+10 chars)")).toBe(true);
  });
});
