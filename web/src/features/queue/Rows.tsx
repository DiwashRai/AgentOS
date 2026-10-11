// The item row (Needs you) and the quiet row (Waiting on others / Blocked / Done recently).
// Click / Enter / Space anywhere on the row opens the peek; the row's buttons keep their action.
import type { Schemas } from "../../api/client";
import { age, isOld, srcLabel } from "../../domain/format";
import { stageModel } from "../../domain/stage";
import type { MeRef } from "../../domain/status";
import { Avatar, cx, StageGlyph, StatusMark } from "../../ui";
import { peekItemProps } from "../peek/usePeek";
import { askLabel, statusMark, subLine, waitingPerson } from "./model";
import s from "./Queue.module.css";
import { RowActions } from "./RowActions";

type Ticket = Schemas["TicketSummary"];

interface RowProps {
  ticket: Ticket;
  me: MeRef;
  now: number;
  onOpen: (key: string) => void;
}

export function Age({ since, now }: { since: string; now: number }) {
  return (
    <span
      className={cx(s.age, isOld(since, now) && s.old)}
      title={new Date(since).toLocaleString()}
    >
      {age(since, now)}
    </span>
  );
}

export function NeedRow({ ticket: t, me, now, onOpen }: RowProps) {
  return (
    <article
      className={cx(s.row, s.lift)}
      {...peekItemProps(t.title, () => {
        onOpen(t.key);
      })}
    >
      <StageGlyph model={stageModel(t)} size="xs" className={s.stage} />
      <span className={s.src}>{srcLabel(t)}</span>
      <div className={s.ti}>
        <span className={s.title} title={t.title}>
          {t.title}
        </span>
        <div className={s.sub}>{subLine(t, me)}</div>
      </div>
      <div className={s.ask}>
        <span>
          <StatusMark kind="you" ask={askLabel(t)} />
        </span>
        <span className={s.askText} title={t.now.text}>
          {t.now.text}
        </span>
      </div>
      <Age since={t.attention_since} now={now} />
      <RowActions ticket={t} />
    </article>
  );
}

function QuietState({ ticket: t, now }: { ticket: Ticket; now: number }) {
  const mark = statusMark(t);
  if (mark.kind === "wait") {
    const who = waitingPerson(t);
    const tail = [mark.ask, t.now.text, age(t.attention_since, now)].filter(Boolean).join(" · ");
    return (
      <span className={s.state}>
        <StatusMark kind="wait" name={mark.name} />
        {who && <Avatar name={who.display_name} id={who.id} />}
        <span title={tail}>{tail}</span>
      </span>
    );
  }
  const text = t.now.text || age(t.attention_since, now);
  return (
    <span className={s.state}>
      <StatusMark kind={mark.kind} name={mark.name} />
      <span title={text}>{text}</span>
    </span>
  );
}

export function QuietRow({ ticket: t, now, onOpen }: RowProps) {
  const blocked = t.attention === "blocked";
  return (
    <article
      className={cx(s.quiet, s.lift, blocked && s.withAct, t.attention === "done" && s.isDone)}
      {...peekItemProps(t.title, () => {
        onOpen(t.key);
      })}
    >
      <StageGlyph model={stageModel(t)} size="xs" className={s.stage} />
      <span className={s.src}>{srcLabel(t)}</span>
      <div className={s.ti}>
        <span className={s.title} title={t.title}>
          {t.title}
        </span>
      </div>
      <QuietState ticket={t} now={now} />
      {blocked && <RowActions ticket={t} primaryOnly />}
    </article>
  );
}
