// The strip slot under the ticket header (DESIGN §7.2): exactly one of the run strip (running or
// queued), the gate strip (parked at a gate) or the status strip. Gate actions are NOT repeated
// here: the header group carries them (gate actions appear once per page).
import { useState } from "react";
import { Link as RouterLink, useLocation } from "react-router";

import type { Schemas } from "../../api/client";
import { elapsed } from "../../domain/format";
import { planSections, planText, sectionCount, showsSections } from "../../domain/plan";
import { OUTCOME_KIND, outcomeOf, resolvedBy, type Outcome } from "../../domain/outcome";
import { statusKind } from "../../domain/status";
import { attentionWords, outcomeWords } from "../../domain/words";
import { RunMeter, StatusMark, cx, linkClass } from "../../ui";
import { isTasksFile } from "../context/files";
import { useNow } from "../runs/useNow";
import {
  gateMeta,
  gatePerson,
  parkedGate,
  planNumber,
  progressStep,
  runLabel,
  runSeconds,
  statusText,
  stripKind,
  ticketPath,
  type ParkedGate,
} from "./model";
import { PrLink } from "./PrLink";
import s from "./Strip.module.css";

type Detail = Schemas["TicketDetail"];

function Elapsed({ startedAt }: { startedAt: string | null }) {
  const now = useNow(1000);
  if (!startedAt) return null;
  return <span className={s.el}>{elapsed(runSeconds(startedAt, now))}</span>;
}

function RunStrip({ t, queued }: { t: Detail; queued: boolean }) {
  const { search } = useLocation();
  const step = queued ? null : progressStep(t.now.progress);
  const plan = t.plan ?? [];
  // Past STRIP_LABEL_MAX steps the meter drops its labels anyway (bare segments).
  const labels = step?.n === plan.length ? plan.map((p) => planText(p.text)) : undefined;
  const nowText = planText(t.now.text);
  return (
    <div className={cx(s.strip, s.run)}>
      <div className={s.who}>
        {queued ? (
          <StatusMark kind="queued" name={attentionWords.queued} />
        ) : (
          <StatusMark kind="run" name={runLabel(t)} />
        )}
        <div className={s.now}>
          <span className={s.nowText} title={nowText}>
            {queued ? "Waiting for a runner" : nowText}
          </span>
          {step ? <span className={s.of}>{`${String(step.k)}/${String(step.n)}`}</span> : null}
          {queued ? null : <Elapsed startedAt={t.latest_run?.started_at ?? null} />}
        </div>
      </div>
      {step ? (
        <RunMeter k={step.k} n={step.n} state="running" size="strip" labels={labels} />
      ) : (
        <span />
      )}
      <RouterLink className={cx(linkClass, s.k)} to={ticketPath(t.key, "runs", search)}>
        Log →
      </RouterLink>
    </div>
  );
}

/** The numbered plan (kit .ao-plan); with 2+ TASKS.md sections, each under a quiet heading. */
function PlanList({ plan }: { plan: Schemas["PlanItem"][] }) {
  const sections = planSections(plan);
  const headed = showsSections(sections);
  return (
    <div className={s.sections}>
      {sections.map((sec, j) => (
        <div key={j}>
          {headed && sec.section !== null ? (
            <div className={s.secHead}>
              <span title={sec.section}>{sec.section}</span>
              <span>{sectionCount(sec)}</span>
            </div>
          ) : null}
          <ol className={s.plan} aria-label={headed ? (sec.section ?? undefined) : undefined}>
            {sec.items.map(({ item, index }) => (
              <li key={index} className={cx(item.done && s.isDone)}>
                <span className={s.k}>{item.done ? "✓" : planNumber(index)}</span>
                <span>{planText(item.text)}</span>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}

function GateStrip({ t, parked, tasksPath }: { t: Detail; parked: ParkedGate; tasksPath: string }) {
  const { gate } = parked;
  const mine = gate.state === "you";
  const params = new URLSearchParams({ file: tasksPath });
  return (
    <div className={cx(s.strip, s.gate)}>
      <div className={s.gateWho}>
        {mine ? (
          <StatusMark kind="you" ask={gate.label} />
        ) : (
          <StatusMark kind="wait" name={gatePerson(gate)} ask={gate.label} />
        )}
        <span className={s.meta}>{gateMeta(t, parked)}</span>
      </div>
      <div className={s.doc}>
        <div className={s.lab}>
          {mine ? "Plan · what you're signing off" : "Plan · what's being signed off"}
        </div>
        {t.plan && t.plan.length > 0 ? (
          <PlanList plan={t.plan} />
        ) : (
          <p className={s.meta}>No plan written yet.</p>
        )}
      </div>
      <div className={s.acts}>
        <RouterLink
          className={cx(linkClass, s.k)}
          to={ticketPath(t.key, "context", `?${params.toString()}`)}
        >
          Edit TASKS.md →
        </RouterLink>
        {t.pr_url ? <PrLink url={t.pr_url} className={s.k} /> : null}
      </div>
    </div>
  );
}

/** A done ticket: Complete (green) or Abandoned (red), who closed it and when, and the note. */
function OutcomeStrip({ t, outcome }: { t: Detail; outcome: Outcome }) {
  const now = useNow(30_000);
  const r = t.resolution;
  return (
    <div className={cx(s.strip, s.status)}>
      <div className={s.who}>
        <StatusMark kind={OUTCOME_KIND[outcome]} name={outcomeWords[outcome]} />
      </div>
      <p className={s.meta}>
        {r ? resolvedBy(r, now) : statusText(t)}
        {r?.note ? (
          <>
            {" — "}
            <span className={s.note}>{r.note}</span>
          </>
        ) : null}
      </p>
      {t.pr_url ? <PrLink url={t.pr_url} className={s.k} /> : <span />}
    </div>
  );
}

function StatusStrip({ t }: { t: Detail }) {
  const outcome = outcomeOf(t);
  if (outcome) return <OutcomeStrip t={t} outcome={outcome} />;
  const kind = statusKind(t);
  const name =
    kind === "wait" && t.now.person ? t.now.person.display_name : attentionWords[t.attention];
  return (
    <div className={cx(s.strip, s.status)}>
      <div className={s.who}>
        {kind === "you" ? (
          <StatusMark kind="you" ask="Your move" />
        ) : (
          <StatusMark kind={kind} name={name} />
        )}
      </div>
      <span className={s.meta}>{statusText(t)}</span>
      {/* "review the result and mark done": the thing to review */}
      {t.pr_url ? <PrLink url={t.pr_url} className={s.k} /> : <span />}
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
        <RunStrip t={t} queued={kind === "queued"} />
      ) : kind === "gate" && parked ? (
        <GateStrip t={t} parked={parked} tasksPath={tasksPath} />
      ) : (
        <StatusStrip t={t} />
      )}
    </div>
  );
}
