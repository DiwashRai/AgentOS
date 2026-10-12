// The expanded compact row (kit Kit.expandedParts): three columns —
//   1. your move / now: the lead, owner and stages, the row's actions, Peek, Open ticket
//   2. the run (live label) or the plan: TASKS.md items + the compact stepper
//   3. Talk: the message count and the last message
// Detail and chat are fetched only while a row is open (one at a time).
import { useNavigate } from "react-router";

import type { Schemas } from "../../api/client";
import { useChat, useTicket } from "../../api/queries";
import { type Action, ticketActions } from "../../domain/actions";
import { planText } from "../../domain/plan";
import { stageModel } from "../../domain/stage";
import { words } from "../../domain/words";
import { Avatar, Button, cx, StatusMark, Stepper, TickBadge } from "../../ui";
import { ActionConfirm } from "../actions/ActionConfirm";
import { useTicketAction } from "../actions/useTicketAction";
import { planView, plainText, stagesText } from "../peek/model";
import { usePeek } from "../peek/usePeek";
import { useNow } from "../runs/useNow";
import { clockTime, fullTime } from "../talk/thread";
import { ticketPath } from "../ticket/model";
import s from "./Compact.module.css";
import { moveText, ownerLine, runLine, talkLabel } from "./model";

type Ticket = Schemas["TicketSummary"];
type Detail = Schemas["TicketDetail"];

/** Plan items shown before "+N more". */
const PLAN_MAX = 6;

/** The row's own buttons (src/domain/actions.ts) minus Open — Peek and Open ticket follow. */
function Actions({ ticket: t }: { ticket: Ticket }) {
  const action = useTicketAction(t);
  const peek = usePeek();
  const navigate = useNavigate();
  const set = ticketActions(t, "row");
  const buttons: Action[] = [...set.secondary, ...(set.primary ? [set.primary] : [])].filter(
    (a) => a.id !== "open",
  );
  const message = action.error ? `Failed · ${action.error.message}` : null;
  return (
    <>
      <div className={s.acts}>
        {buttons.map((a) => (
          <Button
            key={a.id}
            size="sm"
            variant={a.variant === "accent" ? "default" : a.variant}
            title={a.title}
            disabled={a.disabled === true || action.pending}
            onClick={() => {
              action.reset();
              action.run(a);
            }}
          >
            {a.label}
          </Button>
        ))}
        <Button
          size="sm"
          onClick={() => {
            peek.open(t.key);
          }}
        >
          {words.peek}
        </Button>
        <Button
          size="sm"
          onClick={() => {
            void navigate(ticketPath(t.key, "overview"));
          }}
        >
          Open ticket
        </Button>
      </div>
      {message && (
        <p className={s.err} role="alert" title={message}>
          {message}
        </p>
      )}
      <ActionConfirm confirm={action.confirm} className={s.confirm} />
    </>
  );
}

function Plan({
  ticket: t,
  detail,
  error,
}: {
  ticket: Ticket;
  detail: Detail | undefined;
  error: Error | null;
}) {
  const now = useNow(1000);
  const live = t.attention === "running";
  // An empty TASKS.md checklist reads as no plan, not "0 of 0 done".
  const plan = detail?.plan?.length ? planView(detail.plan, PLAN_MAX) : null;
  return (
    <div className={s.col}>
      {live || t.attention === "queued" ? (
        <div className={cx(s.lab, live && s.isLive)}>
          <StatusMark kind={live ? "run" : "queued"} glyphOnly />
          <span>{runLine(t, now)}</span>
        </div>
      ) : (
        <div className={s.lab}>
          <span>Plan</span>
          {plan && <span>{`· ${String(plan.done)} of ${String(plan.total)} done`}</span>}
        </div>
      )}
      {plan ? (
        <ul className={s.steps}>
          {plan.items.map((item, i) => (
            <li key={i} className={cx(item.done && s.isDone)}>
              {item.done ? (
                <TickBadge size={14} label="Done" />
              ) : (
                <span className={s.todo} role="img" aria-label="Open" />
              )}
              <span title={planText(item.text)}>{plainText(planText(item.text))}</span>
            </li>
          ))}
          {plan.more > 0 && <li className={s.moreItems}>{`+${String(plan.more)} more`}</li>}
        </ul>
      ) : (
        <p className={s.sub}>
          {detail ? "No plan yet." : error ? `Can't load the plan · ${error.message}` : "Loading"}
        </p>
      )}
      <Stepper model={stageModel(t)} compact className={s.stepper} />
    </div>
  );
}

function LastMessage({ ticket: t }: { ticket: Ticket }) {
  const chat = useChat(t.key);
  const last = chat.data?.at(-1);
  let body;
  if (last) {
    const at = new Date(last.created_at);
    body = (
      <div className={s.msg}>
        <Avatar name={last.author.display_name} id={last.author.id} size={26} />
        <div>
          <div className={s.who}>
            {last.author.display_name}
            <time dateTime={last.created_at} title={fullTime(at)}>
              {clockTime(at)}
            </time>
          </div>
          <p className={s.bub}>{last.body}</p>
        </div>
      </div>
    );
  } else if (chat.isError) {
    body = <p className={s.sub}>{`Can't load Talk · ${chat.error.message}`}</p>;
  } else if (chat.isPending && t.chat_count > 0) {
    body = <p className={s.sub}>Loading</p>;
  } else {
    body = <p className={s.sub}>Nobody has said anything yet.</p>;
  }
  return (
    <div className={s.col}>
      <div className={s.lab}>{talkLabel(t.chat_count)}</div>
      {body}
    </div>
  );
}

export function Expanded({ id, ticket: t }: { id: string; ticket: Ticket }) {
  // The board's summary drives the row; the detail only adds the plan.
  const query = useTicket(t.key);
  const move = moveText(t);
  return (
    <div id={id} className={s.more}>
      <div className={s.grid}>
        <div className={s.col}>
          <div className={s.lab}>{move.label}</div>
          <p className={s.lead}>{move.lead}</p>
          <p className={s.sub}>{ownerLine(t, stagesText(t.rail))}</p>
          <Actions ticket={t} />
        </div>
        <Plan ticket={t} detail={query.data} error={query.error} />
        <LastMessage ticket={t} />
      </div>
    </div>
  );
}
