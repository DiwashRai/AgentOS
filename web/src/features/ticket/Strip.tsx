// The now band under the ticket header's stepper (refresh mock .ticket__now; DESIGN §7.2's strip
// slot): exactly one of the run band (running or queued), the gate band (parked at a gate, followed
// by the plan being signed off) or the status band. On you it is the `--you-tint` band with the YOU
// pill + the ask; every other state uses the same band, quiet (no blue). Gate actions are NOT
// repeated here: the header group carries them (gate actions appear once per page).
import { useState } from "react";
import { Link as RouterLink, useLocation } from "react-router";

import type { Schemas } from "../../api/client";
import { clientWait } from "../../domain/clients";
import { elapsed } from "../../domain/format";
import { planText } from "../../domain/plan";
import { OUTCOME_KIND, outcomeOf, resolvedBy, type Outcome } from "../../domain/outcome";
import { statusKind } from "../../domain/status";
import { attentionWords, onlineWords, outcomeWords } from "../../domain/words";
import { RunMeter, StatusMark, cx, linkClass } from "../../ui";
import { isTasksFile } from "../context/files";
import { useNow } from "../../ui/useNow";
import {
  gateMeta,
  gatePerson,
  parkedGate,
  progressStep,
  runLabel,
  runSeconds,
  statusText,
  stripKind,
  thenStage,
  ticketPath,
  type ParkedGate,
} from "./model";
import { PlanList } from "./PlanList";
import { PrLink } from "./PrLink";
import s from "./Strip.module.css";

type Detail = Schemas["TicketDetail"];

function Elapsed({ startedAt }: { startedAt: string | null }) {
  const now = useNow(1000);
  if (!startedAt) return null;
  return <span className={s.num}>{elapsed(runSeconds(startedAt, now))}</span>;
}

function RunBand({ t, queued }: { t: Detail; queued: boolean }) {
  const { search } = useLocation();
  const step = queued ? null : progressStep(t.now.progress);
  const plan = t.plan ?? [];
  // Past STRIP_LABEL_MAX steps the meter drops its labels anyway (bare segments).
  const labels = step?.n === plan.length ? plan.map((p) => planText(p.text)) : undefined;
  const nowText = planText(t.now.text);
  // A queued client run says whose client it waits for and whether that client is online.
  const wait = queued ? clientWait(t) : null;
  const text = wait ? wait.text : queued ? "Waiting for a runner" : nowText;
  return (
    <div className={cx(s.band, s.quiet, s.run)}>
      <div className={s.line}>
        {queued ? (
          <StatusMark kind="queued" name={attentionWords.queued} />
        ) : (
          <StatusMark kind="run" name={runLabel(t)} />
        )}
        <span className={s.text} title={text}>
          {text}
        </span>
        {wait ? <span className={s.meta}>{onlineWords[wait.online]}</span> : null}
        {step ? <span className={s.num}>{`${String(step.k)}/${String(step.n)}`}</span> : null}
        {queued ? null : <Elapsed startedAt={t.latest_run?.started_at ?? null} />}
        <span className={s.end}>
          <RouterLink className={linkClass} to={ticketPath(t.key, "runs", search)}>
            Log →
          </RouterLink>
        </span>
      </div>
      {step ? (
        <RunMeter
          k={step.k}
          n={step.n}
          state="running"
          size="strip"
          labels={labels}
          className={s.meter}
        />
      ) : null}
    </div>
  );
}

function GateBand({ t, parked, tasksPath }: { t: Detail; parked: ParkedGate; tasksPath: string }) {
  const { gate } = parked;
  const mine = gate.state === "you";
  const params = new URLSearchParams({ file: tasksPath });
  const then = thenStage(t);
  const text = planText(t.now.text);
  return (
    <>
      <div className={cx(s.band, mine ? s.you : s.quiet)} title={gateMeta(t, parked)}>
        <div className={s.line}>
          {mine ? (
            <StatusMark kind="you" ask={gate.label} />
          ) : (
            <StatusMark kind="wait" name={gatePerson(gate)} ask={gate.label} />
          )}
          {text ? (
            <span className={cx(s.text, s.instr)} title={text}>
              {text}
            </span>
          ) : null}
          <span className={s.end}>
            {then ? <span className={s.then}>{`Then ${then}`}</span> : null}
            <RouterLink
              className={linkClass}
              to={ticketPath(t.key, "context", `?${params.toString()}`)}
            >
              Edit TASKS.md →
            </RouterLink>
            {t.pr_url ? <PrLink url={t.pr_url} /> : null}
          </span>
        </div>
      </div>
      <PlanList
        plan={t.plan}
        id="ticket-gate-plan"
        level={3}
        empty="No plan written yet."
        className={s.plan}
      />
    </>
  );
}

/** A done ticket: Complete (green) or Abandoned (red), who closed it and when, and the note. */
function OutcomeBand({ t, outcome }: { t: Detail; outcome: Outcome }) {
  const now = useNow(30_000);
  const r = t.resolution;
  return (
    <div className={cx(s.band, s.quiet)}>
      <div className={s.line}>
        <StatusMark kind={OUTCOME_KIND[outcome]} name={outcomeWords[outcome]} />
        <span className={cx(s.text, s.wrap)}>
          {r ? resolvedBy(r, now) : statusText(t)}
          {r?.note ? (
            <>
              {" — "}
              <span className={s.note}>{r.note}</span>
            </>
          ) : null}
        </span>
        {t.pr_url ? (
          <span className={s.end}>
            <PrLink url={t.pr_url} />
          </span>
        ) : null}
      </div>
    </div>
  );
}

function StatusBand({ t }: { t: Detail }) {
  const outcome = outcomeOf(t);
  if (outcome) return <OutcomeBand t={t} outcome={outcome} />;
  const kind = statusKind(t);
  const name =
    kind === "wait" && t.now.person ? t.now.person.display_name : attentionWords[t.attention];
  const text = statusText(t);
  return (
    <div className={cx(s.band, kind === "you" ? s.you : s.quiet)}>
      <div className={s.line}>
        {kind === "you" ? (
          <StatusMark kind="you" ask="Your move" />
        ) : (
          <StatusMark kind={kind} name={name} />
        )}
        {text ? (
          <span className={cx(s.text, s.wrap, kind === "you" && s.instr)}>{text}</span>
        ) : null}
        {/* "review the result and mark done": the thing to review */}
        {t.pr_url ? (
          <span className={s.end}>
            <PrLink url={t.pr_url} />
          </span>
        ) : null}
      </div>
    </div>
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
  const parked = parkedGate(t);
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
