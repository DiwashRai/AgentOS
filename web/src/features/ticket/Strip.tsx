// The strip slot under the ticket header (DESIGN §7.2): exactly one of the run strip (running or
// queued), the gate strip (parked at a gate) or the status strip. Gate actions are NOT repeated
// here: the header group carries them (gate actions appear once per page).
import { useState } from "react";
import { Link as RouterLink, useLocation } from "react-router";

import type { Schemas } from "../../api/client";
import { elapsed } from "../../domain/format";
import { statusKind } from "../../domain/status";
import { attentionWords } from "../../domain/words";
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
  const labels = step?.n === plan.length ? plan.map((p) => p.text) : undefined;
  return (
    <div className={cx(s.strip, s.run)}>
      <div className={s.who}>
        {queued ? (
          <StatusMark kind="queued" name={attentionWords.queued} />
        ) : (
          <StatusMark kind="run" name={runLabel(t)} />
        )}
        <div className={s.now}>
          <span className={s.nowText} title={t.now.text}>
            {queued ? "Waiting for a runner" : t.now.text}
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
          <ol className={s.plan}>
            {t.plan.map((p, i) => (
              <li key={i} className={cx(p.done && s.isDone)}>
                <span className={s.k}>{p.done ? "✓" : planNumber(i)}</span>
                <span>{p.text}</span>
              </li>
            ))}
          </ol>
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
      </div>
    </div>
  );
}

function StatusStrip({ t }: { t: Detail }) {
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
      <span />
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
