// The ticket header (DESIGN §7.1): kicker tags + the action group, h1, lead, byline (with the
// owner picker), the full stepper and, only while parked at a gate, "THEN <next stage>".
import { useMemo } from "react";

import type { Schemas } from "../../api/client";
import { useMe, usePatchTicket, usePeople } from "../../api/queries";
import { ticketActions, type Action } from "../../domain/actions";
import { repoShort, srcLabel } from "../../domain/format";
import { stageModel } from "../../domain/stage";
import { Button, ChevronIcon, Stepper, Tag, cx } from "../../ui";
import { useTicketAction } from "../actions/useTicketAction";
import { useNow } from "../runs/useNow";
import s from "./Header.module.css";
import {
  actionErrorText,
  firstParagraph,
  openedText,
  ownerPatch,
  ownerValue,
  pipelineText,
  runnerText,
  thenStage,
} from "./model";

type Detail = Schemas["TicketDetail"];

function ActionGroup({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const set = ticketActions(t, "header");
  const action = useTicketAction(t);
  const button = (a: Action, primary: boolean) => (
    <Button
      key={a.id}
      variant={a.variant}
      size={primary ? "lg" : "md"}
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
        {set.secondary.map((a) => button(a, false))}
        {set.primary ? button(set.primary, true) : null}
      </div>
      {action.error ? (
        <p className={s.actErr} role="alert">
          {actionErrorText(action.error)}
        </p>
      ) : null}
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
  );
}

export function Header({ t }: { t: Detail }) {
  const me = useMe();
  const now = useNow(30_000);
  const readOnly = me.data?.role === "viewer";
  const lead = firstParagraph(t.description);
  const then = thenStage(t);
  return (
    <header className={s.head}>
      <div className={s.top}>
        <div className={s.kicker}>
          <Tag title={t.source?.url}>{srcLabel(t)}</Tag>
          <Tag>{t.type}</Tag>
          {t.priority === "high" ? <Tag prio>High</Tag> : null}
          {t.repo_name ? <Tag title={t.repo_url ?? undefined}>{repoShort(t.repo_name)}</Tag> : null}
        </div>
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
          <dd>{pipelineText(t.pipeline)}</dd>
        </div>
      </dl>
      <div className={s.steps}>
        <Stepper model={stageModel(t)} />
        {then ? (
          <p className={s.then}>
            <span className={cx(s.k)}>Then</span>
            {then}
          </p>
        ) : null}
      </div>
    </header>
  );
}
