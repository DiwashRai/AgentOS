// The run meter's state and its k/n text (kit.js `runOfText`). k is the step in progress, 1-based
// (k/n reads "3/5" while step 3 runs), as in the kit. ui/meter.ts draws the segments.

export type MeterState = "running" | "finished" | "queued";

/** "3/5": a finished run reads n/n, a queued one 0/n; k is clamped into 0..n. */
export function meterOfText(k: number, n: number, state: MeterState): string {
  const count = Math.max(Math.trunc(n), 0);
  const running = Math.min(Math.max(Math.trunc(k), 0), count);
  const done = state === "finished" ? count : state === "queued" ? 0 : running;
  return `${String(done)}/${String(count)}`;
}
