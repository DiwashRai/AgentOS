// One run's log (SPEC §13 Runs): events polled every second while the run is active, summarised
// for reading, pinned to the bottom unless the reader scrolls up.
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";

import { useRunEvents } from "../../api/queries";
import { cx, EmptyState, StatusLine } from "../../ui";
import { buildLines } from "./log";
import { emptyLogText, type LogDelivery } from "./runFormat";
import s from "./RunsTab.module.css";

/** The hook keeps the newest this-many events (SPEC §13: last 5,000 lines). */
const EVENT_CAP = 5000;
/** Within this many px of the bottom counts as "at the bottom". */
const STICK_PX = 32;

const TIME = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function timeOf(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : TIME.format(d);
}

export interface RunLogProps {
  runId: number;
  active: boolean;
  queued: boolean;
  stageName: (id: string | null) => string;
  /** `at_end`: the log arrives in one go when the GitHub job finishes. */
  delivery: LogDelivery;
  /** The run's launcher link (an Actions run), to watch an `at_end` run live. */
  liveHref: string | null;
}

/** Mount with `key={runId}` so scroll state starts fresh for each run. */
export function RunLog({ runId, active, queued, stageName, delivery, liveHref }: RunLogProps) {
  const events = useRunEvents(runId, active);
  const { refetch } = events;
  const lines = useMemo(() => buildLines(events.data ?? [], stageName), [events.data, stageName]);

  // The last events can land between the final poll and the status flip: fetch once more.
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) void refetch();
    wasActive.current = active;
  }, [active, refetch]);

  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const opened = useRef(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || lines.length === 0) return;
    // Open at the newest line; then follow it while the run is live and the reader hasn't scrolled up.
    if (!opened.current || (active && stick.current)) el.scrollTop = el.scrollHeight;
    opened.current = true;
  }, [lines, active]);

  if (events.isPending) return <StatusLine>Loading log</StatusLine>;
  if (events.isError) {
    return <StatusLine error>{`Can't load the log · ${events.error.message}`}</StatusLine>;
  }
  if (lines.length === 0) {
    const waiting = delivery === "at_end" && (queued || active);
    return (
      <EmptyState form="note">
        {emptyLogText({ queued, active, delivery })}
        {waiting && liveHref ? (
          <>
            {" "}
            <a className={s.textLink} href={liveHref} target="_blank" rel="noreferrer">
              Watch it live on GitHub ↗
            </a>
          </>
        ) : null}
      </EmptyState>
    );
  }

  return (
    <>
      {events.data.length >= EVENT_CAP ? (
        <StatusLine>Showing the last 5,000 events</StatusLine>
      ) : null}
      <div
        ref={box}
        className={s.log}
        role="log"
        aria-label={`Run #${String(runId)} log`}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
        }}
      >
        {lines.map((l) => (
          <div key={l.key} className={cx(s.line, s[l.kind])} title={timeOf(l.ts)}>
            {l.kind === "warning" ? <span className={s.warnLabel}>Warning: </span> : null}
            {l.text}
            {l.truncated ? <span className={s.trunc}> (truncated)</span> : null}
          </div>
        ))}
      </div>
    </>
  );
}
