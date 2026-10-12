// My queue rows (refresh HANDOFF §5 "My queue row"): one grid for every zone, so titles share one
// x and stage labels sit at 90px. Needs you / Waiting / Blocked are raised; Done (incl. abandoned)
// is flat. Click / Enter / Space anywhere on the row opens the peek; the row's buttons keep their
// action.
import type { ReactNode } from "react";

import type { Schemas } from "../../api/client";
import { age } from "../../domain/format";
import { stageModel } from "../../domain/stage";
import type { MeRef } from "../../domain/status";
import {
  Age,
  Avatar,
  AvatarSlot,
  cx,
  KeyChip,
  PriorityIcon,
  StageGlyph,
  StatusMark,
  TypeIcon,
} from "../../ui";
import { peekItemProps } from "../peek/usePeek";
import {
  askLabel,
  blockedReason,
  outcomeLine,
  rowLook,
  sourceWord,
  statusMark,
  subLine,
  waitingPerson,
} from "./model";
import s from "./Queue.module.css";
import { RowActions } from "./RowActions";

type Ticket = Schemas["TicketSummary"];

interface RowProps {
  ticket: Ticket;
  me: MeRef;
  now: number;
  onOpen: (key: string) => void;
}

/** The row's age cell: the kit Age, large on Needs-you rows ("38m", "3h 05m"). */
function RowAge({ ticket: t, now }: { ticket: Ticket; now: number }) {
  return (
    <Age
      text={age(t.attention_since, now)}
      size={t.attention === "needs_you" ? "lg" : "md"}
      title={new Date(t.attention_since).toLocaleString()}
      className={s.age}
    />
  );
}

const EDGE = {
  you: s.edgeYou,
  live: s.edgeLive,
  blocked: s.edgeBlocked,
  done: s.edgeDone,
  neutral: s.edgeNeutral,
} as const;

/** The row shell: surface, status edge, wash, lift, and the left half every row shares (stage
 *  glyph, key chip, type/priority icons, title, optional sub-line). */
function RowShell({
  ticket: t,
  sub,
  onOpen,
  children,
}: {
  ticket: Ticket;
  sub?: string | undefined;
  onOpen: (key: string) => void;
  children: ReactNode;
}) {
  const look = rowLook(t);
  return (
    <article
      className={cx(
        s.row,
        look.flat ? s.flat : s.raised,
        t.attention === "needs_you" && s.isYou,
        EDGE[look.edge],
      )}
      {...peekItemProps(`${t.key} ${t.title}`, () => {
        onOpen(t.key);
      })}
    >
      <StageGlyph model={stageModel(t)} className={s.stage} />
      <span className={s.key}>
        <KeyChip>{t.key}</KeyChip>
      </span>
      <div className={s.ti}>
        <div className={s.title}>
          <TypeIcon type={t.type} />
          <PriorityIcon priority={t.priority} />
          <span title={t.title}>{t.title}</span>
        </div>
        {sub ? (
          <div className={s.sub} title={sub}>
            {sub}
          </div>
        ) : null}
      </div>
      <span className={cx(s.divider, look.flat && s.blank)} aria-hidden="true" />
      {children}
    </article>
  );
}

export function NeedRow({ ticket: t, me, now, onOpen }: RowProps) {
  return (
    <RowShell ticket={t} sub={subLine(t, me)} onOpen={onOpen}>
      <div className={s.ask}>
        <div className={s.ask1}>
          <StatusMark kind="you" ask={askLabel(t)} />
        </div>
        {t.now.text ? (
          <div className={s.ask2} title={t.now.text}>
            {t.now.text}
          </div>
        ) : null}
      </div>
      <RowAge ticket={t} now={now} />
      <RowActions ticket={t} />
    </RowShell>
  );
}

/** Waiting on others / Blocked: raised, the status on line one and its text on line two. */
function StateCell({ ticket: t }: { ticket: Ticket }) {
  const mark = statusMark(t);
  if (mark.kind === "wait") {
    const who = waitingPerson(t);
    return (
      <div className={s.ask}>
        <div className={s.ask1}>
          {who ? <Avatar name={who.display_name} id={who.id} /> : null}
          <StatusMark kind="wait" name={mark.name} ask={mark.ask} />
        </div>
        {t.now.text ? (
          <div className={s.ask2} title={t.now.text}>
            {t.now.text}
          </div>
        ) : null}
      </div>
    );
  }
  const text = t.attention === "blocked" ? blockedReason(t) : t.now.text;
  return (
    <div className={s.ask}>
      <div className={s.ask1}>
        <StatusMark kind={mark.kind} name={mark.name} />
      </div>
      {text ? (
        <div className={s.ask2} title={text}>
          {text}
        </div>
      ) : null}
    </div>
  );
}

/** Done recently: flat; the person and repo, the age, and the outcome right-aligned. */
function DoneCells({ ticket: t, now }: { ticket: Ticket; now: number }) {
  const out = outcomeLine(t);
  const src = sourceWord(t);
  const where = [src, t.repo_name].filter(Boolean).join(" · ");
  return (
    <>
      <span className={s.who}>
        {t.owner ? <Avatar name={t.owner.display_name} id={t.owner.id} /> : <AvatarSlot />}
        <span title={where}>{where}</span>
      </span>
      <RowAge ticket={t} now={now} />
      <span className={s.outcome} title={out?.note ?? undefined}>
        {out ? (
          <>
            <span className={cx(out.abandoned && s.abandoned)}>{out.word}</span>
            {out.note ? ` · ${out.note}` : null}
          </>
        ) : null}
      </span>
    </>
  );
}

export function QuietRow({ ticket: t, me, now, onOpen }: RowProps) {
  if (t.attention === "done") {
    return (
      <RowShell ticket={t} onOpen={onOpen}>
        <DoneCells ticket={t} now={now} />
      </RowShell>
    );
  }
  return (
    <RowShell ticket={t} sub={subLine(t, me)} onOpen={onOpen}>
      <StateCell ticket={t} />
      <RowAge ticket={t} now={now} />
      {t.attention === "blocked" ? (
        <RowActions ticket={t} primaryOnly />
      ) : (
        <span className={s.actsBlank} />
      )}
    </RowShell>
  );
}
