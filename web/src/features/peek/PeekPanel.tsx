// The Peek's contents (DESIGN §9): header, plan + compact stepper, run or status, Talk, footer.
// Detail comes from useTicket(key); the board's summary stands in while it loads. The peek never
// carries gate actions (those live on the row or the ticket header).
import { useId } from "react";
import { Link as RouterLink, useNavigate } from "react-router";

import { ApiError, type Schemas } from "../../api/client";
import { useTicket } from "../../api/queries";
import { useBoard } from "../../app/useBoard";
import { firstName, repoShort } from "../../domain/format";
import { resolvedBy } from "../../domain/outcome";
import { firstParagraph, planView } from "../../domain/plan";
import { type GlyphModel, stageModel } from "../../domain/stage";
import {
  outcomeLine,
  runLine,
  sourceRef,
  stageCount,
  statusLine,
  statusMark,
} from "../../domain/ticketView";
import { words } from "../../domain/words";
import {
  Button,
  Checklist,
  cx,
  DataFoot,
  DialogTitle,
  EmptyState,
  Inline,
  linkClass,
  RunMeter,
  Spacer,
  Stack,
  StageGlyph,
  StatusLine,
  StatusMark,
  Stepper,
  text,
} from "../../ui";
import { useNow } from "../../ui/useNow";
import { Talk } from "../talk/Talk";
import { PrLink } from "../ticket/PrLink";
import { ticketPath } from "../ticket/model";
import s from "./Peek.module.css";

type Summary = Schemas["TicketSummary"];
type Detail = Schemas["TicketDetail"];

/** Plan items shown before "+N more". */
const PLAN_MAX = 8;

const ESC_HINT = [{ keys: ["Esc"], label: "close" }] as const;

function Plan({ model, detail }: { model: GlyphModel; detail: Detail | undefined }) {
  const id = useId();
  return (
    <Stack as="section" gap={4} className={s.sec} aria-label="Plan">
      <Checklist
        plan={planView(detail?.plan, PLAN_MAX)}
        density="compact"
        title="Plan"
        level={3}
        id={id}
        empty={detail ? <EmptyState form="note">No plan yet.</EmptyState> : undefined}
      />
      <Stepper model={model} compact />
    </Stack>
  );
}

function Run({ ticket: t }: { ticket: Summary }) {
  const now = useNow(1000);
  const live = t.attention === "running";
  const line = runLine(t, now);
  const { meter } = line;
  return (
    <Stack as="section" gap={4} className={s.sec} aria-label="Run">
      <h3 className={cx(text.label, s.lab, live && s.isLive)}>
        <StatusMark kind={live ? "run" : "queued"} glyphOnly />
        <span className={text.ellipsis}>{line.line}</span>
      </h3>
      {meter && <RunMeter k={meter.k} n={meter.n} state={meter.state} size="tile" />}
      {line.step && <p className={cx(text.meta, s.flat)}>{line.step}</p>}
    </Stack>
  );
}

function Status({ ticket: t }: { ticket: Summary }) {
  const mark = statusMark(t);
  const line = statusLine(t);

  // A closed ticket's note (Complete / Abandoned) says more than the status line; otherwise the
  // dispatch block, flagged, sits above the reason.
  const note = t.resolution?.note ?? line.reason;
  const block = line.flag && !t.resolution?.note ? line.text : null;
  return (
    <Stack as="section" gap={4} className={s.sec} aria-label="Status">
      <h3 className={cx(text.label, s.lab)}>Status</h3>
      <Stack gap={3}>
        <span>
          <StatusMark kind={mark.kind} name={mark.name} ask={mark.ask} />
        </span>
        {block && <p className={cx(text.meta, s.flat, s.flag)}>{block}</p>}
        {note && <p className={cx(text.meta, s.flat, s.note)}>{note}</p>}
      </Stack>
    </Stack>
  );
}

/** "Abandoned by Diwash · 2h ago" in the facts line. */
function ResolvedFact({ word, resolution }: { word: string; resolution: Schemas["Resolution"] }) {
  const now = useNow(30_000);
  return (
    <span title={new Date(resolution.at).toLocaleString()}>
      {`${word} by `}
      <b>{resolvedBy(resolution, now)}</b>
    </span>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <Button size="sm" aria-label="Close peek" onClick={onClose}>
      Esc
    </Button>
  );
}

export function PeekPanel({ ticketKey, onClose }: { ticketKey: string; onClose: () => void }) {
  const navigate = useNavigate();
  const board = useBoard();
  const query = useTicket(ticketKey);
  const detail = query.data;
  const t: Summary | undefined = detail ?? board.tickets.find((x) => x.key === ticketKey);
  const notFound = query.error instanceof ApiError && query.error.status === 404;

  if (notFound || !t) {
    let line = "Loading";
    if (notFound) line = "Ticket not found";
    else if (query.error) line = `Can't reach the server · ${query.error.message}`;

    return (
      <Stack gap={9} className={s.quiet}>
        <Inline justify="end">
          <CloseButton onClose={onClose} />
        </Inline>
        <StatusLine error={Boolean(query.error) && !notFound}>{line}</StatusLine>
      </Stack>
    );
  }

  const desc = detail ? firstParagraph(detail.description) : null;
  const repo = repoShort(t.repo_name);
  const running = t.attention === "running" || t.attention === "queued";
  const runner = t.attention === "running" ? t.latest_run?.launch_ref : null;
  const outcome = outcomeLine(t)?.word;
  const model = stageModel(t);
  const path = ticketPath(t.key, "overview");

  return (
    <>
      <header className={s.head}>
        <Inline gap={4} className={s.bar}>
          <span className={s.mono}>{sourceRef(t) ?? t.key}</span>
          {repo && <span className={s.mono}>{`· ${repo}`}</span>}
          <Spacer />
          <StageGlyph model={model} />
          <Button
            size="sm"
            onClick={() => {
              void navigate(path);
            }}
          >
            {words.open}
          </Button>
          <CloseButton onClose={onClose} />
        </Inline>
        <DialogTitle className={s.title}>{t.title}</DialogTitle>
        {desc?.text && (
          <p className={s.desc} title={desc.raw}>
            {desc.text}
          </p>
        )}
        <Inline wrap gap={8} rowGap={3} className={cx(text.meta, s.facts)}>
          <span>
            Owner <b>{t.owner ? firstName(t.owner) : "Nobody"}</b>
          </span>
          <span>
            Pipeline <b>{stageCount(model)}</b>
          </span>
          {runner && (
            <span>
              Runner <b>{runner}</b>
            </span>
          )}
          {outcome && t.resolution && <ResolvedFact word={outcome} resolution={t.resolution} />}
          {t.pr_url && <PrLink url={t.pr_url} />}
        </Inline>
      </header>
      <Plan model={model} detail={detail} />
      {running ? <Run ticket={t} /> : t.attention !== "inbox" && <Status ticket={t} />}
      <div className={s.talk}>
        <Talk ticketKey={t.key} variant="peek" limit={8} />
      </div>
      <DataFoot rule={false} hints={ESC_HINT} className={s.foot}>
        <RouterLink className={linkClass} to={path}>
          {words.openTicket}
        </RouterLink>
      </DataFoot>
    </>
  );
}
