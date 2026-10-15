// The expanded compact row (kit Kit.expandedParts): three columns —
//   1. your move / now: the lead, owner and stages, the row's actions, Peek, Open ticket
//   2. the run (live label) or the plan: TASKS.md items + the compact stepper
//   3. Talk: the message count and the last message
// Detail and chat are fetched only while a row is open (one at a time).
import { useNavigate } from "react-router";

import type { Schemas } from "../../api/client";
import { useChat, useTicket } from "../../api/queries";
import { type Action, ticketActions } from "../../domain/actions";
import { planView } from "../../domain/plan";
import { stageModel } from "../../domain/stage";
import { runLine, stageCount } from "../../domain/ticketView";
import { words } from "../../domain/words";
import {
  Avatar,
  Button,
  Checklist,
  cx,
  EmptyState,
  Inline,
  Stack,
  StatusLine,
  StatusMark,
  Stepper,
  text,
} from "../../ui";
import { ActionConfirm } from "../actions/ActionConfirm";
import { useTicketAction } from "../actions/useTicketAction";
import { usePeek } from "../peek/usePeek";
import { useNow } from "../../ui/useNow";
import { clockTime, fullTime } from "../talk/thread";
import { ticketPath } from "../ticket/model";
import s from "./Compact.module.css";
import { moveText, ownerLine, talkLabel } from "./model";

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
      <Inline wrap gap={3}>
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
      </Inline>
      {message && (
        <p className={cx(text.error, text.ellipsis, s.err)} role="alert" title={message}>
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
  const run = live || t.attention === "queued";
  const plan = planView(detail?.plan, PLAN_MAX);

  let empty;
  if (detail) empty = <EmptyState form="note">No plan yet.</EmptyState>;
  else if (error) empty = <StatusLine error>{`Can't load the plan · ${error.message}`}</StatusLine>;
  else empty = <StatusLine>Loading</StatusLine>;

  // A live or queued ticket heads the column with its run line; the plan then goes unheaded.
  return (
    <Stack>
      {run ? (
        <Inline gap={3} className={cx(text.label, s.lab, live && s.isLive)}>
          <StatusMark kind={live ? "run" : "queued"} glyphOnly />
          <span className={text.ellipsis}>{runLine(t, now).line}</span>
        </Inline>
      ) : null}
      <Checklist
        plan={plan}
        density="compact"
        title={run ? undefined : "Plan"}
        level={3}
        label="Plan"
        empty={empty}
      />
      <Stepper model={stageModel(t)} compact className={s.stepper} />
    </Stack>
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
          <Inline gap={4} align="baseline" className={s.who}>
            {last.author.display_name}
            <time dateTime={last.created_at} title={fullTime(at)}>
              {clockTime(at)}
            </time>
          </Inline>
          <p className={s.bub}>{last.body}</p>
        </div>
      </div>
    );
  } else if (chat.isError) {
    body = <StatusLine error>{`Can't load Talk · ${chat.error.message}`}</StatusLine>;
  } else if (chat.isPending && t.chat_count > 0) {
    body = <StatusLine>Loading</StatusLine>;
  } else {
    body = <EmptyState form="note">Nobody has said anything yet.</EmptyState>;
  }

  return (
    <Stack>
      <div className={cx(text.label, text.ellipsis, s.lab)}>{talkLabel(t.chat_count)}</div>
      {body}
    </Stack>
  );
}

export function Expanded({ id, ticket: t }: { id: string; ticket: Ticket }) {
  // The board's summary drives the row; the detail only adds the plan.
  const query = useTicket(t.key);
  const move = moveText(t);
  return (
    <div id={id} className={s.more}>
      <div className={s.grid}>
        <Stack>
          <div className={cx(text.label, text.ellipsis, s.lab)}>{move.label}</div>
          <p className={s.lead}>{move.lead}</p>
          <p className={s.sub}>{ownerLine(t, stageCount(t).text)}</p>
          <Actions ticket={t} />
        </Stack>
        <Plan ticket={t} detail={query.data} error={query.error} />
        <LastMessage ticket={t} />
      </div>
    </div>
  );
}
