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
      { seq: 1, ts: TS, stage_id: "plan", type: "stage_started", data: {} },
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
        data: { status: "failed", exit_code: 1, reason: "timeout" },
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
          data: { status: "succeeded", exit_code: 0, reason: null },
        },
      ],
      names,
    );
    expect(lines[0]).toMatchObject({ kind: "stageEnd", text: "Plan · passed · exit 0" });
  });

  it("clips very long raw lines", () => {
    const [line] = buildLines([log(1, "stdout", "x".repeat(RAW_MAX + 10))], names);
    expect(line?.text.endsWith("… (+10 chars)")).toBe(true);
  });
});
