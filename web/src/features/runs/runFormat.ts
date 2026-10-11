// Pure display helpers for the Runs tab. They map server statuses to marks and words and format
// times; they never derive run or board state.
import type { Schemas } from "../../api/client";
import type { MarkKind } from "../../ui";

export type RunStatus = Schemas["RunSummary"]["status"];
export type StageRunStatus = Schemas["StageRunInfo"]["status"];

/** Runs the server is still working on (polled, cancellable). */
export function isActiveRun(status: RunStatus | undefined): boolean {
  return status === "queued" || status === "dispatched" || status === "running";
}

export interface MarkInfo {
  kind: MarkKind;
  word: string;
}

const RUN_MARK: Record<RunStatus, MarkInfo> = {
  queued: { kind: "queued", word: "Queued" },
  dispatched: { kind: "queued", word: "Starting" },
  running: { kind: "run", word: "Running" },
  succeeded: { kind: "done", word: "Passed" },
  gated: { kind: "you", word: "Gated" },
  failed: { kind: "blk", word: "Failed" },
  cancelled: { kind: "blk", word: "Cancelled" },
};

const STAGE_MARK: Record<StageRunStatus, MarkInfo> = {
  pending: { kind: "idle", word: "Pending" },
  running: { kind: "run", word: "Running" },
  succeeded: { kind: "done", word: "Passed" },
  failed: { kind: "blk", word: "Failed" },
  cancelled: { kind: "blk", word: "Cancelled" },
};

export function runMark(status: RunStatus): MarkInfo {
  return RUN_MARK[status];
}

export function stageMark(status: StageRunStatus): MarkInfo {
  return STAGE_MARK[status];
}

/** `0:42`, `12:05` under an hour; `1h 4m` from there. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 3600) return `${String(Math.floor(s / 60))}:${String(s % 60).padStart(2, "0")}`;
  return `${String(Math.floor(s / 3600))}h ${String(Math.floor((s % 3600) / 60))}m`;
}

/** Seconds from `start` to `end` (or to `nowMs` while unfinished); null if it never started. */
export function spanSeconds(
  start: string | null,
  end: string | null,
  nowMs: number,
): number | null {
  if (start === null) return null;
  const from = Date.parse(start);
  const to = end === null ? nowMs : Date.parse(end);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  return Math.max(0, (to - from) / 1000);
}

/** Where a run was launched: the launcher's link (`launch_url`: a GitHub Actions run, or the
 *  workflow's runs page when GitHub didn't name the run), else its ref as mono text (a PID). */
export function launchRef(
  run: Pick<Schemas["RunSummary"], "launch_ref" | "launch_url">,
): { href: string | null; text: string } | null {
  if (run.launch_url) {
    const one = /\/actions\/runs\/\d+\/?$/.test(run.launch_url);
    return { href: run.launch_url, text: one ? "Actions run ↗" : "Actions ↗" };
  }
  const r = run.launch_ref?.trim() ?? "";
  if (r === "") return null;
  if (/^\d+$/.test(r)) return { href: null, text: `pid ${r}` };
  return { href: null, text: r };
}
