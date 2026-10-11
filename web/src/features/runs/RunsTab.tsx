// Ticket page · Runs tab (SPEC §13 Runs, DESIGN §7.3): run history newest first, and for the
// selected run (`?run=<id>`, default the latest) its stages and its log. The Manifest view and
// "load earlier" for logs over 5,000 events come later.
import { useCallback, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router";

import { ApiError, type Schemas } from "../../api/client";
import { useCancelRun, useRun, useRuns } from "../../api/queries";
import { age } from "../../domain/format";
import { Button, cx, StatusMark } from "../../ui";
import { RunLog } from "./RunLog";
import {
  duration,
  isActiveRun,
  launchRef,
  runMark,
  spanSeconds,
  stageMark,
  type MarkInfo,
} from "./runFormat";
import s from "./RunsTab.module.css";
import { useNow } from "./useNow";

type RunSummary = Schemas["RunSummary"];
type RunDetail = Schemas["RunDetail"];

/** A run's mark and label. Gated uses the "you" glyph but not the YOU pill: a run doesn't know
 *  who the gate waits on. */
function RunMark({ id, mark }: { id: number; mark: MarkInfo }) {
  const name = `Run #${String(id)}`;
  if (mark.kind === "you") {
    return (
      <span className={s.gated}>
        <StatusMark kind="you" glyphOnly title={mark.word} />
        <b>{name}</b>
        <span>· {mark.word}</span>
      </span>
    );
  }
  return <StatusMark kind={mark.kind} name={name} ask={mark.word} />;
}

function startedText(run: RunSummary, now: number): string {
  const at = run.started_at ?? run.created_at;
  const a = age(at, now);
  return a === "now" ? "just now" : `${a} ago`;
}

function RunRow({
  run,
  selected,
  now,
  onSelect,
}: {
  run: RunSummary;
  selected: boolean;
  now: number;
  onSelect: () => void;
}) {
  const secs = spanSeconds(run.started_at, run.finished_at, now);
  const ref = launchRef(run.launch_ref);
  return (
    <li className={cx(s.row, selected && s.selected)}>
      <button type="button" className={s.rowMain} aria-current={selected} onClick={onSelect}>
        <RunMark id={run.id} mark={runMark(run.status)} />
        <span className={s.rowMeta}>
          <span className={s.ellip}>
            from <b>{run.from_stage}</b>
            {run.created_by ? ` · ${run.created_by.display_name}` : ""} · {startedText(run, now)}
          </span>
          {secs !== null ? <span className={s.mono}>{duration(secs)}</span> : null}
        </span>
      </button>
      <span className={s.ref}>
        {ref?.href ? (
          <a className={s.refLink} href={ref.href} target="_blank" rel="noreferrer">
            {ref.text}
          </a>
        ) : ref ? (
          <span className={s.mono} title={run.launch_ref ?? undefined}>
            {ref.text}
          </span>
        ) : null}
      </span>
    </li>
  );
}

function CancelControl({ ticketKey, run }: { ticketKey: string; run: RunSummary | RunDetail }) {
  const cancel = useCancelRun(ticketKey);
  if (run.cancel_requested_at !== null || cancel.isPending) {
    return <span className={s.k}>Cancelling…</span>;
  }
  return (
    <span className={s.cancel}>
      {cancel.isError ? (
        <span className={s.err} role="alert">
          {cancel.error instanceof ApiError && cancel.error.code === "run_gone"
            ? "This run already finished."
            : cancel.error.message}
        </span>
      ) : null}
      <Button
        variant="quiet"
        size="sm"
        onClick={() => {
          cancel.mutate(run.id);
        }}
      >
        Cancel run
      </Button>
    </span>
  );
}

function StageList({ detail, now }: { detail: RunDetail; now: number }) {
  if (detail.stage_runs.length === 0) return null;
  return (
    <ol className={s.stages} aria-label="Stages">
      {[...detail.stage_runs]
        .sort((a, b) => a.idx - b.idx)
        .map((st) => {
          const mark = stageMark(st.status);
          const secs = spanSeconds(st.started_at, st.finished_at, now);
          const note = [
            st.exit_code !== null && st.status !== "succeeded"
              ? `exit ${String(st.exit_code)}`
              : "",
            st.reason ?? "",
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <li key={st.idx} className={s.stage}>
              <StatusMark kind={mark.kind} glyphOnly title={mark.word} />
              <span className={s.stageName}>{st.name}</span>
              <span className={cx(s.stageNote, mark.kind === "blk" && s.bad)} title={note}>
                {note || mark.word}
              </span>
              <span className={s.mono}>{secs !== null ? duration(secs) : ""}</span>
            </li>
          );
        })}
    </ol>
  );
}

function RunPanel({
  ticketKey,
  summary,
  now,
}: {
  ticketKey: string;
  summary: RunSummary;
  now: number;
}) {
  const detailQ = useRun(summary.id, isActiveRun(summary.status));
  // The detail polls every second while active, so it is fresher than the 3 s list. If the list
  // sees the run finish first, polling stops: fetch the detail once more so it doesn't stay live.
  const { refetch } = detailQ;
  const detailStatus = detailQ.data?.status;
  useEffect(() => {
    if (!isActiveRun(summary.status) && isActiveRun(detailStatus)) void refetch();
  }, [summary.status, detailStatus, refetch]);
  const run: RunSummary | RunDetail = detailQ.data ?? summary;
  const active = isActiveRun(run.status);
  const stageRuns = detailQ.data?.stage_runs;
  const stageName = useCallback(
    (id: string | null) => {
      if (id === null) return "Run";
      return stageRuns?.find((st) => st.stage_id === id)?.name ?? id;
    },
    [stageRuns],
  );
  const secs = spanSeconds(run.started_at, run.finished_at, now);

  return (
    <section className={s.panel} aria-label={`Run #${String(run.id)}`}>
      <div className={s.panelHead}>
        <div className={s.panelWho}>
          <RunMark id={run.id} mark={runMark(run.status)} />
          <span className={s.meta}>
            from <b>{run.from_stage}</b>
            {run.created_by ? ` · ${run.created_by.display_name}` : ""} · {startedText(run, now)}
            {secs !== null ? ` · ${duration(secs)}` : ""}
          </span>
        </div>
        {active ? <CancelControl ticketKey={ticketKey} run={run} /> : null}
      </div>
      {run.error ? (
        <p className={s.runError} role="note">
          {run.error}
        </p>
      ) : null}
      {detailQ.isError ? (
        <p className={s.quiet} data-error="">
          {`Can't load stages · ${detailQ.error.message}`}
        </p>
      ) : detailQ.data ? (
        <StageList detail={detailQ.data} now={now} />
      ) : null}
      <RunLog
        key={run.id}
        runId={run.id}
        active={active}
        queued={run.status === "queued" || run.status === "dispatched"}
        stageName={stageName}
      />
    </section>
  );
}

export function RunsTab({ ticketKey }: { ticketKey: string }) {
  const runsQ = useRuns(ticketKey);
  const [params, setParams] = useSearchParams();
  const runs = useMemo(() => [...(runsQ.data ?? [])].sort((a, b) => b.id - a.id), [runsQ.data]);
  const anyActive = runs.some((r) => isActiveRun(r.status));
  const now = useNow(anyActive ? 1000 : 30_000);

  if (runsQ.isPending) return <p className={s.quiet}>Loading runs</p>;
  if (runsQ.isError) {
    return (
      <p className={s.quiet} data-error="" role="alert">
        {`Can't load runs · ${runsQ.error.message}`}
      </p>
    );
  }
  if (runs.length === 0) {
    return <p className={s.empty}>No runs yet. Dispatch one from the header.</p>;
  }

  const asked = Number(params.get("run"));
  const selected = runs.find((r) => r.id === asked) ?? runs[0];
  const select = (id: number) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("run", String(id));
        return next;
      },
      { replace: true },
    );
  };

  return (
    <div className={s.tab}>
      <ul className={s.runs} aria-label="Runs">
        {runs.map((r) => (
          <RunRow
            key={r.id}
            run={r}
            now={now}
            selected={r.id === selected?.id}
            onSelect={() => {
              select(r.id);
            }}
          />
        ))}
      </ul>
      {selected ? (
        <RunPanel key={selected.id} ticketKey={ticketKey} summary={selected} now={now} />
      ) : null}
    </div>
  );
}
