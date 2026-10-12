// Intake (contract request 14, design/intake-mock.html): the Jira issues matching the Space's
// intake queries that aren't AgentOS tickets yet, one group per query, each row a "Take in" away
// from the Inbox (POST /api/tickets/import). Rows are Compact-style lines; the key opens Jira, the
// button takes it in, so the row itself is not a click target (it tints on hover, it doesn't lift).
// Every list and number comes from model.ts; this view only lays them out.
import { useEffect, useId, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, useImportTicket, useIntake, useRefreshIntake } from "../../api/queries";
import { OpenIcon } from "../../app/icons";
import { Quiet } from "../../app/Quiet";
import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useSearchQuery } from "../../app/useSearchQuery";
import type { Query } from "../../domain/search";
import { Avatar, Button, cx, GroupHead, Kbd, linkClass, LinkButton, StatusMark } from "../../ui";
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
  toTakeIn,
  typeText,
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

function Assignee({ person }: { person: IntakeItem["assignee"] }) {
  if (!person)
    return (
      <span className={cx(s.as, s.isNone)}>
        <i className={s.nobody} aria-hidden="true" />
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
  const { type, priority } = typeText(item);
  return (
    <li className={cx(s.row, taken && s.isTaken)}>
      <a
        className={s.key}
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        title={`Open ${item.key} in Jira`}
      >
        JIRA <b>{item.key}</b>
        <OpenIcon />
      </a>
      <span className={s.type}>
        {type}
        {priority && <em> · {priority}</em>}
      </span>
      <span className={s.ti} title={item.title}>
        {item.title}
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
          <span className={s.st} title={`Jira status: ${item.status}`}>
            {item.status}
          </span>
          <Assignee person={item.assignee} />
          <span
            className={s.upd}
            title={`Updated in Jira ${new Date(item.updated_at).toLocaleString()}`}
          >
            {ageText(item, now)}
          </span>
          <span className={s.act}>
            <Button
              size="sm"
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
  else if (items.length === 0)
    body = <p className={s.empty}>{emptyGroupText(q, query.length > 0)}</p>;
  else
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
      <GroupHead
        name={q.name}
        count={q.error ? undefined : groupCount(items, takenKeys)}
        summary={summary ?? undefined}
        collapsed={collapsed}
        onToggle={onToggle}
        controls={bodyId}
      />
      <div id={bodyId} hidden={collapsed}>
        <div className={s.jql}>
          {/* Not uppercased: JQL values are case-sensitive. */}
          <code title={q.jql}>{q.jql}</code>
          {href && (
            <a className={s.out} href={href} target="_blank" rel="noopener noreferrer">
              Open in Jira <OpenIcon />
            </a>
          )}
        </div>
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
  return (
    <div className={s.page}>
      <TopBar
        title="Intake"
        middle={
          intake.data && (
            <div className={s.counters} aria-live="polite">
              <span>
                <b>{total}</b> to take in
              </span>
              <span className={cx(s.sep, s.wide)} />
              <span className={s.wide}>
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
            <Kbd>/</Kbd> search
          </span>
          <span className={s.keys}>
            <Kbd>Esc</Kbd> clear
          </span>
          <span className={s.grow} />
          {intake.data && (
            <span className={s.note}>{footNote(queries, intake.dataUpdatedAt, now)}</span>
          )}
          {fresh.error && <span className={s.note}>Refresh failed · {fresh.error.message}</span>}
          <LinkButton
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
