// The now band under the ticket header's stepper (refresh mock .ticket__now; DESIGN §7.2's strip
// slot): exactly one of the run band (running or queued), the gate band (parked at a gate, followed
// by the plan being signed off) or the status band. On you it is the `--you-tint` band with the YOU
// pill + the ask; every other state uses the same band, quiet (no blue). Gate actions are NOT
// repeated here: the header group carries them (gate actions appear once per page).
import { useState, type ReactNode } from "react";
import { Link as RouterLink, useLocation } from "react-router";

import type { Schemas } from "../../api/client";
import { planView } from "../../domain/plan";
import { OUTCOME_KIND, resolvedBy } from "../../domain/outcome";
import {
  gate,
  type GateView,
  outcomeLine,
  type OutcomeLine,
  runLine,
  statusLine,
  statusMark,
} from "../../domain/ticketView";
import { attentionWords, onlineWords } from "../../domain/words";
import {
  Checklist,
  EmptyState,
  Inline,
  RunMeter,
  Stack,
  StatusMark,
  cx,
  linkClass,
  text,
} from "../../ui";
import { isTasksFile } from "../context/files";
import { useNow } from "../../ui/useNow";
import { gateMeta, stripKind, thenStage, ticketPath } from "./model";
import { PrLink } from "./PrLink";
import s from "./Strip.module.css";

type Detail = Schemas["TicketDetail"];

/** The status band's line for a ticket nobody has dispatched yet. */
const INBOX_LINE = "Nobody on it yet · add context, then dispatch";

/** One band: the quiet surface, or the you tint; the line first, then what sits under it. */
function Band({
  you = false,
  title,
  line,
  children,
}: {
  you?: boolean;
  title?: string | undefined;
  line: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Stack gap={5} className={cx(s.band, you ? s.you : s.quiet)} title={title}>
      <Inline wrap gap={4} rowGap={3} className={s.line}>
        {line}
      </Inline>
      {children}
    </Stack>
  );
}

/** The right end of a band's line: Then <stage>, Edit TASKS.md, the PR, the log. */
function End({ children }: { children: ReactNode }) {
  return (
    <Inline as="span" gap={7} className={s.end}>
      {children}
    </Inline>
  );
}

/** A number or word beside the line (k/n, the elapsed time, a waiting client's Online). */
function Num({ children }: { children: ReactNode }) {
  return <span className={cx(text.meta, s.num)}>{children}</span>;
}

function RunBand({ t, queued }: { t: Detail; queued: boolean }) {
  const { search } = useLocation();
  const now = useNow(1000);
  const line = runLine(t, now);
  const meter = queued ? null : line.meter;
  const plan = planView(t.plan);
  // Past STRIP_LABEL_MAX steps the meter drops its labels anyway (bare segments).
  const labels = plan && meter?.n === plan.total ? plan.items.map((p) => p.text) : undefined;
  // A queued client run says whose client it waits for and whether that client is online.
  const wait = queued ? line.wait : null;
  const words = wait ? wait.text : queued ? "Waiting for a runner" : line.step;
  return (
    <Band
      line={
        <>
          {queued ? (
            <StatusMark kind="queued" name={attentionWords.queued} />
          ) : (
            <StatusMark kind="run" name={line.label} />
          )}
          <span className={cx(text.ellipsis, s.text)} title={words}>
            {words}
          </span>
          {wait ? <Num>{onlineWords[wait.online]}</Num> : null}
          {meter ? <Num>{meter.text}</Num> : null}
          {queued || line.elapsed === null ? null : <Num>{line.elapsed}</Num>}
          <End>
            <RouterLink className={linkClass} to={ticketPath(t.key, "runs", search)}>
              Log →
            </RouterLink>
          </End>
        </>
      }
    >
      {meter ? (
        <RunMeter
          k={meter.k}
          n={meter.n}
          state={meter.state}
          size="strip"
          labels={labels}
          className={s.meter}
        />
      ) : null}
    </Band>
  );
}

function GateBand({ t, parked, tasksPath }: { t: Detail; parked: GateView; tasksPath: string }) {
  const mine = parked.state === "you";
  const params = new URLSearchParams({ file: tasksPath });
  const then = thenStage(t);
  const step = runLine(t, null).step;
  return (
    <>
      <Band
        you={mine}
        title={gateMeta(t, parked)}
        line={
          <>
            {mine ? (
              <StatusMark kind="you" ask={parked.label} />
            ) : (
              <StatusMark kind="wait" name={parked.person} ask={parked.label} />
            )}
            {step ? (
              <span className={cx(text.ellipsis, s.text, s.instr)} title={step}>
                {step}
              </span>
            ) : null}
            <End>
              {then ? <span className={text.meta}>{`Then ${then}`}</span> : null}
              <RouterLink
                className={linkClass}
                to={ticketPath(t.key, "context", `?${params.toString()}`)}
              >
                Edit TASKS.md →
              </RouterLink>
              {t.pr_url ? <PrLink url={t.pr_url} /> : null}
            </End>
          </>
        }
      />
      <section className={s.plan} aria-labelledby="ticket-gate-plan">
        <Checklist
          plan={planView(t.plan)}
          title="Plan"
          level={3}
          id="ticket-gate-plan"
          empty={<EmptyState form="note">No plan written yet.</EmptyState>}
        />
      </section>
    </>
  );
}

/** A done ticket: Complete (green) or Abandoned (red), who closed it and when, and the note. */
function OutcomeBand({ t, outcome }: { t: Detail; outcome: OutcomeLine }) {
  const now = useNow(30_000);
  const r = t.resolution;
  return (
    <Band
      line={
        <>
          <StatusMark kind={OUTCOME_KIND[outcome.outcome]} name={outcome.word} />
          <span className={cx(text.ellipsis, s.text, s.wrap)}>
            {r ? resolvedBy(r, now) : statusLine(t).reason}
            {r?.note ? (
              <>
                {" — "}
                <span className={s.note}>{r.note}</span>
              </>
            ) : null}
          </span>
          {t.pr_url ? (
            <End>
              <PrLink url={t.pr_url} />
            </End>
          ) : null}
        </>
      }
    />
  );
}

function StatusBand({ t }: { t: Detail }) {
  const outcome = outcomeLine(t);
  if (outcome) return <OutcomeBand t={t} outcome={outcome} />;

  const mark = statusMark(t);
  const kind = mark.kind;
  const line = t.attention === "inbox" ? INBOX_LINE : statusLine(t).reason;
  return (
    <Band
      you={kind === "you"}
      line={
        <>
          <StatusMark kind={kind} name={mark.name} ask={mark.ask} />
          {line ? (
            <span className={cx(text.ellipsis, s.text, s.wrap, kind === "you" && s.instr)}>
              {line}
            </span>
          ) : null}
          {/* "review the result and mark done": the thing to review */}
          {t.pr_url ? (
            <End>
              <PrLink url={t.pr_url} />
            </End>
          ) : null}
        </>
      }
    />
  );
}

export function Strip({ t, files }: { t: Detail; files: readonly { path: string }[] }) {
  const kind = stripKind(t);
  // Fade + rise only when the kind changes, never on first paint.
  const [shown, setShown] = useState(kind);
  const [changed, setChanged] = useState(false);
  if (shown !== kind) {
    setShown(kind);
    setChanged(true);
  }
  const parked = gate(t);
  const tasksPath = files.find((f) => isTasksFile(f.path))?.path ?? "TASKS.md";
  return (
    <div key={kind} className={cx(s.slot, changed && s.enter)}>
      {kind === "run" || kind === "queued" ? (
        <RunBand t={t} queued={kind === "queued"} />
      ) : kind === "gate" && parked ? (
        <GateBand t={t} parked={parked} tasksPath={tasksPath} />
      ) : (
        <StatusBand t={t} />
      )}
    </div>
  );
}
