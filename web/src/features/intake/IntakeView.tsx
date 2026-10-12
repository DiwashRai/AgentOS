// Intake (contract request 14; visual refresh HANDOFF §5 "Intake row"): the Jira issues matching
// the Space's intake queries that aren't AgentOS tickets yet, one group per query, each row a
// "Take in" away from the Inbox (POST /api/tickets/import). Every row is raised; the key opens
// Jira, the button takes it in, so the row itself is not a click target: it lights up on hover or
// focus (never moves) and Take in fills blue. Every list and number comes from model.ts; this view
// only lays them out.
import { useEffect, useId, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, useImportTicket, useIntake, useRefreshIntake } from "../../api/queries";
import { Quiet } from "../../app/Quiet";
import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useSearchQuery } from "../../app/useSearchQuery";
import type { Query } from "../../domain/search";
import {
  Age,
  Avatar,
  AvatarSlot,
  Button,
  cx,
  EmptyRow,
  GroupHead,
  Kbd,
  KeyChip,
  linkClass,
  LinkButton,
  litRowClass,
  PriorityIcon,
  StatusMark,
  TypeIcon,
} from "../../ui";
import { importErrorText } from "../newticket/importForm";
import { useNow } from "../runs/useNow";
import s from "./Intake.module.css";
import {
  ageText,
  capText,
  emptyGroupText,
  footNote,
  groupCount,
  headSummary,
  type IntakeItem,
  type IntakeQuery,
  jqlHref,
  nameId,
  searchItems,
  sharedPrefix,
  toTakeIn,
  visibleRows,
} from "./model";

/** Ages tick by the minute; nothing here shows seconds. */
const AGE_TICK_MS = 30_000;
/** How long "Taken in" stays before the list is refetched (and the row leaves). */
const TAKEN_HOLD_MS = 3500;

const COLUMNS = ["Jira", "Type", "Issue", "Jira status", "Assignee", "Upd", ""] as const;

/** A row taken in on this page: the ticket it became, and whether it already existed. */
interface Taken {
  ticketKey: string;
  created: boolean;
}
type TakenMap = ReadonlyMap<string, Taken>;

/** The small "↗" of a link out to Jira (the mock's form: an arrow, no box). */
function OutArrow() {
  return (
    <svg
      className={s.arrow}
      width="10"
      height="10"
      viewBox="0 0 10 10"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2.5 7.5 7.5 2.5M3.5 2.5h4v4" />
    </svg>
  );
}

function Assignee({ person }: { person: IntakeItem["assignee"] }) {
  if (!person)
    return (
      <span className={cx(s.as, s.isNone)}>
        <AvatarSlot dashed />
        <span>Unassigned</span>
      </span>
    );
  return (
    <span className={s.as}>
      <Avatar name={person.display_name} id={nameId(person.display_name)} />
      <span title={person.display_name}>{person.display_name}</span>
    </span>
  );
}

function IntakeRow({
  item,
  now,
  taken,
  onTaken,
}: {
  item: IntakeItem;
  now: number;
  taken: Taken | undefined;
  onTaken: (key: string, result: Schemas["ImportResult"]) => void;
}) {
  const take = useImportTicket();
  return (
    <li className={cx(s.row, taken ? s.isTaken : litRowClass)}>
      <a
        className={s.key}
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        title={`Open ${item.key} in Jira`}
      >
        <KeyChip>{item.key}</KeyChip>
        <OutArrow />
      </a>
      <TypeIcon type={item.type} label className={s.type} />
      <span className={s.ti}>
        <PriorityIcon priority={item.priority} />
        <span title={item.title}>{item.title}</span>
      </span>
      {taken ? (
        <span className={s.taken} role="status">
          <StatusMark kind="done" name={taken.created ? "Taken in" : "Already here"} />
          <span className={s.where}>{taken.created ? "Inbox · yours" : "Taken in before"}</span>
          <RouterLink className={linkClass} to={`/t/${encodeURIComponent(taken.ticketKey)}`}>
            Open {taken.ticketKey} →
          </RouterLink>
        </span>
      ) : (
        <>
          <span className={s.st}>
            {/* Jira gives only the status name (no category), so one neutral ring for all. */}
            <span className={s.loz} title={`Jira status: ${item.status}`}>
              <i aria-hidden="true" />
              <span>{item.status}</span>
            </span>
          </span>
          <Assignee person={item.assignee} />
          <span
            className={s.upd}
            title={`Updated in Jira ${new Date(item.updated_at).toLocaleString()}`}
          >
            <Age text={ageText(item, now)} />
          </span>
          <span className={s.act}>
            <Button
              variant="outline"
              size="sm"
              litOnRow
              disabled={take.isPending}
              aria-busy={take.isPending || undefined}
              aria-label={take.isPending ? undefined : `Take in ${item.key}`}
              onClick={() => {
                take.mutate(
                  { ref: item.key },
                  {
                    onSuccess: (r) => {
                      onTaken(item.key, r);
                    },
                  },
                );
              }}
            >
              {take.isPending ? "Taking in…" : "Take in"}
            </Button>
          </span>
        </>
      )}
      {take.isError && !taken && (
        <p className={s.rowErr} role="alert">
          {importErrorText(take.error)}
        </p>
      )}
    </li>
  );
}

function IntakeGroup({
  q,
  query,
  jiraUrl,
  now,
  taken,
  takenKeys,
  collapsed,
  expanded,
  retrying,
  onToggle,
  onExpand,
  onRetry,
  onTaken,
}: {
  q: IntakeQuery;
  query: Query;
  jiraUrl: string | null;
  now: number;
  taken: TakenMap;
  takenKeys: ReadonlySet<string>;
  collapsed: boolean;
  expanded: boolean;
  retrying: boolean;
  onToggle: () => void;
  onExpand: () => void;
  onRetry: () => void;
  onTaken: (key: string, result: Schemas["ImportResult"]) => void;
}) {
  const bodyId = useId();
  const items = searchItems(q.items, query);
  const { shown, rest } = visibleRows(items, expanded);
  const href = jqlHref(jiraUrl, q.jql);
  const cap = capText(q);
  const summary = headSummary(q);
  const prefix = sharedPrefix(q.items);

  let body;
  if (q.error)
    body = (
      <div className={s.err} role="status">
        <span className={s.errK}>Jira didn&apos;t answer</span>
        <span className={s.msg}>{q.error}</span>
        <span className={s.hint}>
          An admin can fix the query in{" "}
          <RouterLink className={linkClass} to="/settings/intake">
            Settings › Intake
          </RouterLink>
        </span>
        <LinkButton className={s.errRetry} disabled={retrying} onClick={onRetry}>
          {retrying ? "Retrying…" : "Retry"}
        </LinkButton>
      </div>
    );
  else if (items.length === 0) {
    const { lead, note } = emptyGroupText(q, query.length > 0);
    body = <EmptyRow lead={lead}>{note}</EmptyRow>;
  } else
    body = (
      <>
        <ul className={s.rows}>
          {shown.map((item) => (
            <IntakeRow
              key={item.key}
              item={item}
              now={now}
              taken={taken.get(item.key)}
              onTaken={onTaken}
            />
          ))}
        </ul>
        {rest > 0 && (
          <div className={s.more}>
            <b>+{rest} more</b>
            <span className={s.moreEnd}>
              {cap && <span className={s.cap}>{cap}</span>}
              <LinkButton onClick={onExpand}>Show all {items.length}</LinkButton>
            </span>
          </div>
        )}
      </>
    );

  return (
    <section className={s.group} aria-label={q.name}>
      <div className={s.head}>
        <GroupHead
          className={s.gh}
          name={q.name}
          icon={prefix ? <KeyChip size="sm">{prefix}</KeyChip> : undefined}
          count={q.error ? undefined : groupCount(items, takenKeys)}
          summary={
            <>
              {summary && <span className={s.of}>{summary} ·</span>}
              {/* Not uppercased: JQL values are case-sensitive. */}
              <code className={s.jql} title={q.jql}>
                {q.jql}
              </code>
            </>
          }
          collapsed={collapsed}
          onToggle={onToggle}
          controls={bodyId}
        />
        {href && (
          <a
            className={s.out}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Open ${q.name} in Jira`}
            title="Open in Jira"
          >
            <OutArrow />
          </a>
        )}
      </div>
      <div id={bodyId} hidden={collapsed}>
        {body}
      </div>
    </section>
  );
}

function toggled<T>(set: ReadonlySet<T>, v: T): ReadonlySet<T> {
  const next = new Set(set);
  if (!next.delete(v)) next.add(v);
  return next;
}

export function IntakeView() {
  const intake = useIntake();
  const fresh = useRefreshIntake();
  const refreshing = intake.isFetching || fresh.isPending;
  const jiraUrl = useConfig().data?.jira_url ?? null;
  const { query } = useSearchQuery();
  const now = useNow(AGE_TICK_MS);

  const [taken, setTaken] = useState<TakenMap>(new Map());
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const queries = useMemo(() => intake.data ?? [], [intake.data]);
  const takenKeys = useMemo(() => new Set(taken.keys()), [taken]);
  const total = useMemo(() => toTakeIn(queries, takenKeys), [queries, takenKeys]);

  // "Taken in" holds for a moment, then a refetch drops the row (the server leaves it out now).
  const { refetch } = intake;
  useEffect(() => {
    if (taken.size === 0) return;
    const id = window.setTimeout(() => {
      void refetch();
    }, TAKEN_HOLD_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [taken, refetch]);

  const onTaken = (key: string, r: Schemas["ImportResult"]) => {
    setTaken((prev) => new Map(prev).set(key, { ticketKey: r.ticket.key, created: r.created }));
  };

  let body;
  if (intake.isPending) body = <Quiet>Loading</Quiet>;
  else if (intake.error)
    body = <Quiet error>{`Can't reach the server · ${intake.error.message}`}</Quiet>;
  else if (queries.length === 0)
    body = (
      <p className={s.none}>
        No intake queries yet. An admin adds them in{" "}
        <RouterLink className={linkClass} to="/settings/intake">
          Settings › Intake
        </RouterLink>
        .
      </p>
    );
  else
    body = queries.map((q) => (
      <IntakeGroup
        key={q.id}
        q={q}
        query={query}
        jiraUrl={jiraUrl}
        now={now}
        taken={taken}
        takenKeys={takenKeys}
        collapsed={collapsed.has(q.id)}
        expanded={expanded.has(q.id)}
        retrying={refreshing}
        onToggle={() => {
          setCollapsed((c) => toggled(c, q.id));
        }}
        onExpand={() => {
          setExpanded((e) => toggled(e, q.id));
        }}
        onRetry={() => {
          fresh.mutate();
        }}
        onTaken={onTaken}
      />
    ));

  const n = queries.length;
  const foot = intake.data ? footNote(queries, intake.dataUpdatedAt, now) : null;
  return (
    <div className={s.page}>
      <TopBar
        title="Intake"
        middle={
          intake.data && (
            <div className={s.counters} aria-live="polite">
              <span className={cx(s.counter, total === 0 && s.isZero)}>
                <b>{total}</b> to take in
              </span>
              <span className={cx(s.counter, s.wide, n === 0 && s.isZero)}>
                <b>{n}</b> {n === 1 ? "query" : "queries"}
              </span>
            </div>
          )
        }
      />
      <Surface className={s.surface}>
        <div className={s.colhead} aria-hidden="true">
          {COLUMNS.map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </div>
        <div className={s.list}>{body}</div>
        <footer className={s.foot}>
          <span className={s.keys}>
            <Kbd>/</Kbd> Search
          </span>
          <span className={s.keys}>
            <Kbd>Esc</Kbd> Clear
          </span>
          <span className={s.grow} />
          {foot && (
            <span className={s.note}>
              <b>{foot.count}</b> {foot.text}
            </span>
          )}
          {fresh.error && <span className={s.note}>Refresh failed · {fresh.error.message}</span>}
          <LinkButton
            className={s.refresh}
            disabled={refreshing}
            onClick={() => {
              fresh.mutate();
            }}
          >
            {refreshing && !intake.isPending ? "Refreshing…" : "Refresh"}
          </LinkButton>
        </footer>
      </Surface>
    </div>
  );
}
