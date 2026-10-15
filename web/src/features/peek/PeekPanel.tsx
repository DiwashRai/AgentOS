// The Peek's contents (DESIGN §9): header, plan + compact stepper, run or status, Talk, footer.
// Detail comes from useTicket(key); the board's summary stands in while it loads. The peek never
// carries gate actions (those live on the row or the ticket header).
import { Link as RouterLink, useNavigate } from "react-router";

import { ApiError, type Schemas } from "../../api/client";
import { useTicket } from "../../api/queries";
import { useBoard } from "../../app/useBoard";
import { duration, firstName, repoShort, runSeconds } from "../../domain/format";
import { outcomeWord, resolvedBy } from "../../domain/outcome";
import { planText } from "../../domain/plan";
import { stageModel } from "../../domain/stage";
import { words } from "../../domain/words";
import { Button, cx, Kbd, linkClass, RunMeter, StageGlyph, StatusMark, Stepper } from "../../ui";
import { meterOfText } from "../../ui/meter";
import { meterOf, runNumber, sourceWord, statusMark } from "../queue/model";
import { useNow } from "../../ui/useNow";
import { Talk } from "../talk/Talk";
import { PrLink } from "../ticket/PrLink";
import { firstParagraph, plainText, planView, stagesText } from "./model";
import s from "./Peek.module.css";

type Summary = Schemas["TicketSummary"];
type Detail = Schemas["TicketDetail"];

function ticketPath(key: string): string {
  return `/t/${encodeURIComponent(key)}`;
}

function Plan({ ticket: t, detail }: { ticket: Summary; detail: Detail | undefined }) {
  const plan = detail?.plan ? planView(detail.plan) : null;
  return (
    <section className={s.sec} aria-label="Plan">
      <h3 className={s.lab}>
        <span>Plan</span>
        {plan && (
          <span className={s.labNote}>{`${String(plan.done)} of ${String(plan.total)} done`}</span>
        )}
      </h3>
      {plan ? (
        <ul className={s.steps}>
          {plan.items.map((item, i) => (
            <li key={i} className={cx(item.done && s.isDone)}>
              <span className={s.tick} role="img" aria-label={item.done ? "Done" : "Open"}>
                {item.done ? "✓" : "○"}
              </span>
              <span title={planText(item.text)}>{plainText(planText(item.text))}</span>
            </li>
          ))}
          {plan.more > 0 && <li className={s.more}>{`+${String(plan.more)} more`}</li>}
        </ul>
      ) : detail ? (
        <p className={s.meta}>No plan yet.</p>
      ) : null}
      <Stepper model={stageModel(t)} compact />
    </section>
  );
}

function Run({ ticket: t }: { ticket: Summary }) {
  const now = useNow(1000);
  const live = t.attention === "running";
  const meter = meterOf(t);
  const secs = runSeconds(t.latest_run, now);
  const run = runNumber(t);
  const line = [
    t.now.actor ?? "Run",
    run !== null ? `Run #${String(run)}` : null,
    secs !== null ? duration(secs) : null,
    meter ? meterOfText(meter.k, meter.n, meter.state) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <section className={s.sec} aria-label="Run">
      <h3 className={cx(s.lab, live && s.isLive)}>
        <StatusMark kind={live ? "run" : "queued"} glyphOnly />
        <span>{line}</span>
      </h3>
      {meter && <RunMeter k={meter.k} n={meter.n} state={meter.state} size="tile" />}
      {t.now.text && <p className={s.meta}>{planText(t.now.text)}</p>}
    </section>
  );
}

function Status({ ticket: t }: { ticket: Summary }) {
  const mark = statusMark(t);
  // A closed ticket's note (Complete / Abandoned) says more than the server's status line.
  const text = t.resolution?.note ?? t.now.text;
  return (
    <section className={s.sec} aria-label="Status">
      <h3 className={s.lab}>Status</h3>
      <div className={s.status}>
        <span>
          <StatusMark kind={mark.kind} name={mark.name} ask={mark.ask} />
        </span>
        {text && <p>{text}</p>}
      </div>
    </section>
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
    let text = "Loading";
    if (notFound) text = "Ticket not found";
    else if (query.error) text = `Can't reach the server · ${query.error.message}`;
    return (
      <>
        <div className={s.quietBar}>
          <CloseButton onClose={onClose} />
        </div>
        <p className={s.quiet} data-error={query.error && !notFound ? "" : undefined}>
          {text}
        </p>
      </>
    );
  }

  const desc = detail ? firstParagraph(detail.description) : "";
  const repo = repoShort(t.repo_name);
  const running = t.attention === "running" || t.attention === "queued";
  const runner = t.attention === "running" ? t.latest_run?.launch_ref : null;
  const stages = stagesText(t.rail);
  const outcome = outcomeWord(t);

  return (
    <>
      <header className={s.head}>
        <div className={s.bar}>
          <span className={s.src}>{sourceWord(t) ?? t.key}</span>
          {repo && <span className={s.k}>{`· ${repo}`}</span>}
          <span className={s.sp} />
          <StageGlyph model={stageModel(t)} />
          <Button
            size="sm"
            onClick={() => {
              void navigate(ticketPath(t.key));
            }}
          >
            {words.open}
          </Button>
          <CloseButton onClose={onClose} />
        </div>
        <h2 className={s.title}>{t.title}</h2>
        {desc && <p className={s.desc}>{desc}</p>}
        <div className={s.facts}>
          <span>
            Owner <b>{t.owner ? firstName(t.owner) : "Nobody"}</b>
          </span>
          <span>
            Pipeline <b>{stages}</b>
          </span>
          {runner && (
            <span>
              Runner <b>{runner}</b>
            </span>
          )}
          {outcome && t.resolution && <ResolvedFact word={outcome} resolution={t.resolution} />}
          {t.pr_url && <PrLink url={t.pr_url} />}
        </div>
      </header>
      <Plan ticket={t} detail={detail} />
      {running ? <Run ticket={t} /> : t.attention !== "inbox" && <Status ticket={t} />}
      <div className={s.talk}>
        <Talk ticketKey={t.key} variant="peek" limit={8} />
      </div>
      <footer className={s.foot}>
        <span className={s.keys}>
          <Kbd>Esc</Kbd> close
        </span>
        <span className={s.sp} />
        <RouterLink className={linkClass} to={ticketPath(t.key)}>
          {words.openTicket}
        </RouterLink>
      </footer>
    </>
  );
}
