// Pure display helpers for RunMeter (kit.js `segs` / `runOfText`).
// k is the step in progress, 1-based (k/n reads "3/5" while step 3 runs), as in the kit.

export type MeterState = "running" | "finished" | "queued";
export type SegState = "done" | "cur" | "up";

function clamp(k: number, n: number): number {
  return Math.min(Math.max(Math.trunc(k), 0), n);
}

export function meterSegments(k: number, n: number, state: MeterState): SegState[] {
  const count = Math.max(Math.trunc(n), 0);
  const cur = clamp(k, count) - 1;
  return Array.from({ length: count }, (_, i): SegState => {
    if (state === "finished" || i < cur) return "done";
    if (state === "running" && i === cur) return "cur";
    return "up";
  });
}

/** "3/5": a finished run reads n/n, a queued one 0/n. */
export function meterOfText(k: number, n: number, state: MeterState): string {
  const count = Math.max(Math.trunc(n), 0);
  const done = state === "finished" ? count : state === "queued" ? 0 : clamp(k, count);
  return `${String(done)}/${String(count)}`;
}

/** The strip's mono label over each step: NOW, NEXT (the one after it while running), else STEP i. */
export function stripLabel(segs: SegState[], i: number, state: MeterState): string {
  if (segs[i] === "cur") return "Now";
  if (state === "running" && i > 0 && segs[i - 1] === "cur") return "Next";
  return `Step ${String(i + 1)}`;
}
