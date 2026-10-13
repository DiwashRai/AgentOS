// Ticket page · Runs tab (SPEC §13 Runs, DESIGN §7.3): run history newest first, and for the
// selected run (`?run=<id>`, default the latest) its stages, how each stage was called (from the
// run's manifest) and its log. "Load earlier" for logs over 5,000 events comes later.
import { useCallback, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router";

import { ApiError, type Schemas } from "../../api/client";
import { useCancelRun, useRun, useRunEvents, useRuns } from "../../api/queries";
import { age } from "../../domain/format";
import { Button, ChevronIcon, cx, StatusMark } from "../../ui";
import {
  checkFold,
  checkMark,
  foldChecks,
  groupByRound,
  roundCounts,
  roundKey,
  roundMeta,
  verdictWord,
  type CheckOutcome,
  type StageRun,
} from "./rounds";
import { RunLog } from "./RunLog";
import { duration, isActiveRun, launchRef, runMark, spanSeconds, type MarkInfo } from "./runFormat";
import s from "./RunsTab.module.css";
import { StageCalls } from "./StageCalls";
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

function CancelControl({ ticketKey, run }: { ticketKey: string; run: RunSummary }) {
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
        aria-label={`Cancel run #${String(run.id)}`}
        onClick={() => {
          cancel.mutate(run.id);
        }}
      >
        Cancel run
      </Button>
    </span>
  );
}

/** A run's row: the run's one summary line (the panel below doesn't repeat it), plus Cancel run
 *  while the run is active. */
function RunRow({
  ticketKey,
  run,
  selected,
  now,
  onSelect,
}: {
  ticketKey: string;
  run: RunSummary;
  selected: boolean;
  now: number;
  onSelect: () => void;
}) {
  const secs = spanSeconds(run.started_at, run.finished_at, now);
  const ref = launchRef(run);
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
      {isActiveRun(run.status) ? <CancelControl ticketKey={ticketKey} run={run} /> : <span />}
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

/** A check's notes and repairs, folded under its row. */
function CheckFold({ check }: { check: CheckOutcome }) {
  const fold = checkFold(check);
  if (!fold) return null;
  return (
    <details className={cx(s.fold, s.checkFold)}>
      <summary className={s.foldHead}>
        <span className={s.chev}>
          <ChevronIcon />
        </span>
        {fold.label}
        {fold.meta ? <span className={s.foldMeta}>· {fold.meta}</span> : null}
      </summary>
      {check.notes ? <pre className={s.pre}>{check.notes}</pre> : null}
      {check.repairs.length > 0 ? (
        <ol className={s.repairs} aria-label="Repairs">
          {check.repairs.map((r) => (
            <li key={r.attempt}>
              <span className={s.k}>Repair {r.attempt}</span> {r.error}
            </li>
          ))}
        </ol>
      ) : null}
    </details>
  );
}

function StageRow({
  st,
  check,
  now,
}: {
  st: StageRun;
  check: CheckOutcome | undefined;
  now: number;
}) {
  const mark = checkMark(st);
  const secs = spanSeconds(st.started_at, st.finished_at, now);
  const verdict = st.verdict;
  // A check with a verdict ran fine: its exit code says nothing the verdict doesn't.
  const note = [
    st.exit_code !== null && st.status !== "succeeded" && !verdict
      ? `exit ${String(st.exit_code)}`
      : "",
    st.reason ?? "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className={s.stageItem}>
      <div className={s.stage}>
        <StatusMark kind={mark.kind} glyphOnly title={mark.word} />
        <span className={s.stageName}>{st.name}</span>
        <span className={s.noteCell}>
          {verdict ? (
            <span className={cx(s.verdict, verdict === "pass" ? s.vPass : s.vFail)}>
              {verdictWord(verdict)}
            </span>
          ) : null}
          {verdict && !note ? null : (
            <span
              className={cx(
                s.stageNote,
                !verdict && mark.kind === "blk" && s.bad,
                !verdict && mark.kind === "done" && s.ok,
              )}
              title={note}
            >
              {note || mark.word}
            </span>
          )}
        </span>
        <span className={s.mono}>{secs !== null ? duration(secs) : ""}</span>
      </div>
      {check ? <CheckFold check={check} /> : null}
    </li>
  );
}

function StageList({ detail, now, active }: { detail: RunDetail; now: number; active: boolean }) {
  // The same query as the log below (one fetch): the verdicts' notes and repairs.
  const events = useRunEvents(detail.id, active);
  const checks = useMemo(() => foldChecks(events.data ?? []), [events.data]);
  const items = useMemo(() => groupByRound(detail.stage_runs), [detail.stage_runs]);
  if (items.length === 0) return null;
  const row = (st: StageRun) => (
    <StageRow key={st.idx} st={st} check={checks.get(roundKey(st.stage_id, st.round))} now={now} />
  );
  return (
    <ol className={s.stages} aria-label="Stages">
      {items.map((it) => {
        if (it.type === "stage") return row(it.run);
        const meta = roundMeta(roundCounts(it.runs));
        return (
          <li key={it.key} className={s.round}>
            <div className={s.roundHead}>
              <span>Round {it.round}</span>
              {meta ? <span className={s.roundMeta}>{meta}</span> : null}
            </div>
            <ol className={s.roundRows} aria-label={`Round ${String(it.round)}`}>
              {it.runs.map(row)}
            </ol>
          </li>
        );
      })}
    </ol>
  );
}

function RunPanel({ summary, now }: { summary: RunSummary; now: number }) {
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

  // The selected row above is the run's header (mark, who, when, Cancel run): start at its error,
  // stages and log.
  return (
    <section className={s.panel} aria-label={`Run #${String(run.id)}`}>
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
        <StageList detail={detailQ.data} now={now} active={active} />
      ) : null}
      <StageCalls runId={run.id} status={run.status} />
      <RunLog
        key={run.id}
        runId={run.id}
        active={active}
        queued={run.status === "queued" || run.status === "dispatched"}
        stageName={stageName}
        delivery={run.log_delivery}
        liveHref={run.launch_url}
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
            ticketKey={ticketKey}
            run={r}
            now={now}
            selected={r.id === selected?.id}
            onSelect={() => {
              select(r.id);
            }}
          />
        ))}
      </ul>
      {selected ? <RunPanel key={selected.id} summary={selected} now={now} /> : null}
    </div>
  );
}
