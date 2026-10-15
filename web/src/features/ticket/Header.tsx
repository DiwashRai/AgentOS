// The ticket header (DESIGN §7.1 in the visual refresh's form, mock "Ticket page (header)"):
// kicker (key chip, source, type, priority, repo) + the action group, h1, lead, byline (owner
// picker, repo picker, base branch for a repo ticket, runner, opened, the pipeline count) and the
// full-width stepper. The now band and the plan under it are the Strip.
import { useMemo } from "react";

import type { Schemas } from "../../api/client";
import { useMe, usePatchTicket, usePeople } from "../../api/queries";
import { isDispatch, ticketActions, type Action } from "../../domain/actions";
import { repoShort } from "../../domain/format";
import { stageModel } from "../../domain/stage";
import {
  Avatar,
  Button,
  ChevronIcon,
  KeyChip,
  PriorityIcon,
  Stepper,
  Tag,
  TypeIcon,
} from "../../ui";
import { ActionConfirm } from "../actions/ActionConfirm";
import { TargetPicker } from "../actions/TargetPicker";
import { useTicketAction } from "../actions/useTicketAction";
import { useNow } from "../runs/useNow";
import { BaseBranchField } from "./BaseBranchField";
import s from "./Header.module.css";
import { PrLink } from "./PrLink";
import { RepoPicker } from "./RepoPicker";
import {
  actionErrorText,
  firstParagraph,
  openedText,
  ownerPatch,
  ownerValue,
  pipelineSummary,
  runnerText,
  sourceText,
} from "./model";

type Detail = Schemas["TicketDetail"];

function ActionGroup({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const set = ticketActions(t, "header");
  const action = useTicketAction(t);
  // The target choice sits beside a button that dispatches (Dispatch run, Approve, Retry).
  const dispatching = [...set.secondary, set.primary].some(
    (a) => a !== null && isDispatch(a.id) && a.disabled !== true,
  );
  const button = (a: Action) => (
    <Button
      key={a.id}
      variant={a.variant}
      title={readOnly ? "Viewers can't change tickets" : a.title}
      disabled={a.disabled === true || readOnly || action.pending}
      onClick={() => {
        action.reset();
        action.run(a);
      }}
    >
      {a.label}
    </Button>
  );
  return (
    <div className={s.actsCol}>
      <div className={s.acts}>
        {dispatching && !readOnly ? (
          <TargetPicker target={action.target} disabled={action.pending} />
        ) : null}
        {set.secondary.map(button)}
        {set.primary ? button(set.primary) : null}
      </div>
      {action.error ? (
        <p className={s.actErr} role="alert">
          {actionErrorText(action.error)}
        </p>
      ) : null}
      <ActionConfirm confirm={action.confirm} className={s.confirm} />
    </div>
  );
}

function OwnerPicker({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const people = usePeople();
  const patch = usePatchTicket(t.key);
  const options = useMemo(() => {
    const list = people.data ?? [];
    // Keep the current owner selectable even if the people list hasn't loaded (or omits them).
    const all = t.owner && !list.some((p) => p.id === t.owner?.id) ? [t.owner, ...list] : list;
    return [...all].sort((a, b) => a.display_name.localeCompare(b.display_name));
  }, [people.data, t.owner]);
  return (
    <span className={s.owner}>
      {t.owner ? <Avatar name={t.owner.display_name} id={t.owner.id} /> : null}
      <span className={s.pick}>
        <select
          aria-label="Owner"
          value={ownerValue(t.owner)}
          disabled={readOnly || patch.isPending}
          title={patch.error ? actionErrorText(patch.error) : undefined}
          aria-invalid={patch.error ? true : undefined}
          onChange={(e) => {
            patch.mutate(ownerPatch(e.target.value));
          }}
        >
          <option value="">Unassigned</option>
          {options.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.display_name}
            </option>
          ))}
        </select>
        {readOnly ? null : <ChevronIcon />}
      </span>
    </span>
  );
}

function Kicker({ t }: { t: Detail }) {
  const source = sourceText(t);
  return (
    <div className={s.kicker}>
      <KeyChip size="lg">{t.key}</KeyChip>
      {source && t.source?.url ? (
        <a
          className={s.srcLink}
          href={t.source.url}
          target="_blank"
          rel="noreferrer"
          title={t.source.url}
        >
          <Tag>{`${source} ↗`}</Tag>
        </a>
      ) : source ? (
        <Tag>{source}</Tag>
      ) : null}
      <Tag>
        <TypeIcon type={t.type} label />
      </Tag>
      {t.priority === "high" ? (
        <Tag>
          <PriorityIcon priority="high" />
          High
        </Tag>
      ) : null}
      {t.repo_name ? (
        <Tag quiet title={t.repo_url ?? undefined}>
          {repoShort(t.repo_name)}
        </Tag>
      ) : null}
    </div>
  );
}

export function Header({ t }: { t: Detail }) {
  const me = useMe();
  const now = useNow(30_000);
  const readOnly = me.data?.role === "viewer";
  const lead = firstParagraph(t.description);
  const model = stageModel(t);
  return (
    <header className={s.head}>
      <div className={s.top}>
        <Kicker t={t} />
        <ActionGroup t={t} readOnly={readOnly} />
      </div>
      <h1 className={s.h1}>{t.title}</h1>
      {lead ? <p className={s.lead}>{lead}</p> : null}
      <dl className={s.byline}>
        <div>
          <dt>Owner</dt>
          <dd>
            <OwnerPicker t={t} readOnly={readOnly} />
          </dd>
        </div>
        <div className={s.grows}>
          <dt>Repo</dt>
          <dd>
            <RepoPicker t={t} readOnly={readOnly} />
          </dd>
        </div>
        {t.repo_name ? (
          <div className={s.grows}>
            <dt>Base branch</dt>
            <dd>
              <BaseBranchField t={t} readOnly={readOnly} />
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Runner</dt>
          <dd>{runnerText(t)}</dd>
        </div>
        <div>
          <dt>Opened</dt>
          <dd title={new Date(t.created_at).toLocaleString()}>{openedText(t, now)}</dd>
        </div>
        <div>
          <dt>Pipeline</dt>
          <dd>{pipelineSummary(model.stages)}</dd>
        </div>
        {t.pr_url ? (
          <div>
            <dt>Pull request</dt>
            <dd>
              <PrLink url={t.pr_url} />
            </dd>
          </div>
        ) : null}
      </dl>
      <Stepper model={model} className={s.steps} />
    </header>
  );
}
