// My queue rows (refresh HANDOFF §5 "My queue row"): one grid for every zone, so titles share one
// x and stage labels sit at 90px. Needs you (incl. blocked) and Ready to start are raised; Done
// (incl. abandoned) is flat. Click / Enter / Space on the row opens the peek; its buttons keep
// their action.
import type { ReactNode } from "react";

import type { Schemas } from "../../api/client";
import { stageModel } from "../../domain/stage";
import type { MeRef } from "../../domain/status";
import {
  outcomeLine,
  rowLook,
  sourceRef,
  statusLine,
  statusMark,
  waitAge,
} from "../../domain/ticketView";
import {
  Age,
  Avatar,
  AvatarSlot,
  cx,
  DataRow,
  DataTitle,
  Inline,
  KeyChip,
  Stack,
  StageGlyph,
  StatusMark,
  text,
} from "../../ui";
import { peekItemProps } from "../peek/usePeek";
import { subLine } from "./model";
import s from "./Queue.module.css";
import { RowActions } from "./RowActions";

type Ticket = Schemas["TicketSummary"];

interface RowProps {
  ticket: Ticket;
  me: MeRef;
  now: number;
  onOpen: (key: string) => void;
}

/** The row's age cell: the kit Age, large in the Needs you zone (needs-you and blocked rows:
 *  "38m", "3h 05m"). */
function RowAge({ ticket: t, now }: { ticket: Ticket; now: number }) {
  return (
    <Age
      text={waitAge(t, now)}
      size={t.attention === "needs_you" || t.attention === "blocked" ? "lg" : "md"}
      title={new Date(t.attention_since).toLocaleString()}
      className={s.age}
    />
  );
}

/** The row shell: the kit row (surface, status edge, wash, lift) on the queue's grid, and the left
 *  half every row shares (stage glyph, key chip, type/priority icons, title, optional sub-line). */
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
    <DataRow
      as="article"
      className={s.row}
      surface={look.flat ? "flat" : "raised"}
      you={t.attention === "needs_you"}
      edge={look.edge}
      hover="lift"
      {...peekItemProps(`${t.key} ${t.title}`, () => {
        onOpen(t.key);
      })}
    >
      <StageGlyph model={stageModel(t)} className={s.stage} />
      <Inline as="span" className={s.key}>
        <KeyChip>{t.key}</KeyChip>
      </Inline>
      <div className={s.ti}>
        <DataTitle
          as="div"
          text={t.title}
          type={t.type}
          priority={t.priority}
          size={look.flat ? "md" : "lg"}
        />
        {sub ? (
          <div className={cx(s.sub, text.meta, text.ellipsis)} title={sub}>
            {sub}
          </div>
        ) : null}
      </div>
      <span className={cx(s.divider, look.flat && s.blank)} aria-hidden="true" />
      {children}
    </DataRow>
  );
}

/** The line under a status mark (`statusLine`): a dispatch block ("Set a base branch first") in
 *  the you hue, else the reason. */
function SecondLine({ ticket: t }: { ticket: Ticket }) {
  const line = statusLine(t);
  if (!line.text) return null;
  return (
    <div className={cx(s.ask2, text.ellipsis, line.flag && s.flag)} title={line.text}>
      {line.text}
    </div>
  );
}

/** The status cell: the mark (and a face) on line one, its text on line two. */
function AskCell({ ticket: t, children }: { ticket: Ticket; children: ReactNode }) {
  return (
    <Stack className={s.ask}>
      <Inline gap={3} className={s.ask1}>
        {children}
      </Inline>
      <SecondLine ticket={t} />
    </Stack>
  );
}

export function NeedRow({ ticket: t, me, now, onOpen }: RowProps) {
  return (
    <RowShell ticket={t} sub={subLine(t, me)} onOpen={onOpen}>
      <AskCell ticket={t}>
        <StatusMark kind="you" ask={statusMark(t).ask} />
      </AskCell>
      <RowAge ticket={t} now={now} />
      <RowActions ticket={t} />
    </RowShell>
  );
}

/** Waiting on others / Blocked: raised, the status on line one and its text on line two. */
function StateCell({ ticket: t }: { ticket: Ticket }) {
  const mark = statusMark(t);
  if (mark.kind === "wait") {
    const who = mark.person;
    return (
      <AskCell ticket={t}>
        {who ? <Avatar name={who.display_name} id={who.id} /> : null}
        <StatusMark kind="wait" name={mark.name} ask={mark.ask} />
      </AskCell>
    );
  }
  return (
    <AskCell ticket={t}>
      <StatusMark kind={mark.kind} name={mark.name} />
    </AskCell>
  );
}

/** Done recently: flat; the person and repo, the age, and the outcome right-aligned. */
function DoneCells({ ticket: t, now }: { ticket: Ticket; now: number }) {
  const out = outcomeLine(t);
  const src = sourceRef(t);
  const where = [src, t.repo_name].filter(Boolean).join(" · ");
  return (
    <>
      <Inline as="span" gap={5} className={cx(s.who, text.meta)}>
        {t.owner ? <Avatar name={t.owner.display_name} id={t.owner.id} /> : <AvatarSlot />}
        <span className={text.ellipsis} title={where}>
          {where}
        </span>
      </Inline>
      <RowAge ticket={t} now={now} />
      <span className={cx(s.outcome, text.ellipsis)} title={out?.note ?? undefined}>
        {out ? (
          <>
            <span className={cx(out.outcome === "abandoned" && s.abandoned)}>{out.word}</span>
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
