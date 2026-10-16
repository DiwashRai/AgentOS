// Pure display helpers for the Runs tab. They map server statuses to marks and words and format
// times; they never derive run or board state.
import type { Schemas } from "../../api/client";
import { formatSize, plural } from "../../domain/format";
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

/** A run's mark; its word carries the server's `queue_reason` while a queued run has one. */
export function runMark(run: Pick<Schemas["RunSummary"], "status" | "queue_reason">): MarkInfo {
  const mark = RUN_MARK[run.status];
  return run.queue_reason ? { ...mark, word: `${mark.word} · ${run.queue_reason}` } : mark;
}

export function stageMark(status: StageRunStatus): MarkInfo {
  return STAGE_MARK[status];
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

/** When a run's log reaches the server (per run, from the server): `live` (the worker posts events
 *  as it goes) or `at_end` (mailbox runs: the outbox is replayed once the GitHub job finishes). */
export type LogDelivery = Schemas["RunSummary"]["log_delivery"];

/** What an empty log says. */
export function emptyLogText(o: {
  queued: boolean;
  active: boolean;
  delivery: LogDelivery;
}): string {
  if (o.delivery === "at_end" && (o.queued || o.active)) {
    return `${o.queued ? "Queued. " : ""}Logs arrive when the GitHub job finishes.`;
  }
  if (o.queued) return "Queued. The log starts when a worker picks it up.";
  return o.active ? "Waiting for output" : "No output";
}

/** The download link for the files a run returned: `sync_back.zip · 3 files · 1.2 KB`. */
export function syncBackLink(
  runId: number,
  syncBack: Schemas["SyncBackInfo"],
): { href: string; text: string } {
  const files = `${String(syncBack.files)} ${plural(syncBack.files, "file")}`;
  return {
    href: `/api/runs/${String(runId)}/sync-back`,
    text: ["sync_back.zip", files, formatSize(syncBack.bytes)].join(" · "),
  };
}

/** Where a run was launched: the launcher's link (`launch_url`: a GitHub Actions run, or the
 *  workflow's runs page when GitHub didn't name the run), else its ref as mono text: a PID, or the
 *  name of the client that took a client run. */
export function launchRef(
  run: Pick<Schemas["RunSummary"], "launch_ref" | "launch_url" | "target">,
): { href: string | null; text: string } | null {
  if (run.launch_url) {
    const one = /\/actions\/runs\/\d+\/?$/.test(run.launch_url);
    return { href: run.launch_url, text: one ? "Actions run ↗" : "Actions ↗" };
  }
  const r = run.launch_ref?.trim() ?? "";
  if (r === "") return null;
  if (run.target === "client") return { href: null, text: `client ${r}` };
  if (/^\d+$/.test(r)) return { href: null, text: `pid ${r}` };
  return { href: null, text: r };
}
