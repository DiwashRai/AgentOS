// Compact (DESIGN §6, refresh HANDOFF §5): every ticket, one line each, grouped by attention,
// filtered by `?f=` (one group), `?repo=` (one repo) and the search `?q=`. The only list that shows
// the Inbox. A row expands in place, one at a time; Esc collapses it (when no peek is open); J/K
// move the keyboard between rows. Every list and number comes from model.ts over useBoard(); this
// view only lays them out.
// Cut from the prototype: P/O keys, the runner pool gauge, FLIP glides, team bands (repos instead).
import { Fragment, type ReactNode, type RefObject, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { isShortcut } from "../../app/keys";
import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useBoard } from "../../app/useBoard";
import { useSearchQuery } from "../../app/useSearchQuery";
import { repoShort } from "../../domain/format";
import { bySearch } from "../../domain/search";
import type { Attention } from "../../domain/status";
import {
  Age,
  ArrowIcon,
  Badge,
  DataFoot,
  DataHead,
  DataRows,
  type DataColumn,
  dialogOpen,
  EmptyState,
  GroupHead,
  Inline,
  type KeyHint,
  KeyChip,
  SegmentedControl,
  type SegmentOption,
  StatusLine,
  StatusMark,
  useDocumentKey,
} from "../../ui";
import { usePeek } from "../peek/usePeek";
import { NewTicketPrompt, NoMatches } from "../queue/QueueView";
import { useNow } from "../../ui/useNow";
import s from "./Compact.module.css";
import {
  byFilter,
  collapsedByDefault,
  compactGroups,
  compactSections,
  emptyText,
  FILTER_PARAM,
  type Filter,
  footSummary,
  type GroupRight,
  inRepo,
  parseFilter,
  type Pill,
  pills,
  plural,
  REPO_PARAM,
  withFilter,
} from "./model";
import { CompactRow } from "./Row";

/** Ages tick by the minute; nothing on a compact line shows seconds. */
const AGE_TICK_MS = 30_000;

/** Column heads, with the class that hides a column on a narrow surface. */
const COLUMNS: readonly DataColumn[] = [
  { label: "Ref" },
  { label: "Stage", className: s.cStage },
  { label: "Ticket" },
  { label: "Now", className: s.cNow },
  { label: "Source", className: s.cSrc },
  { label: "People", className: s.cPeople },
  { label: "Upd", end: true },
];

const HINTS: readonly KeyHint[] = [
  { keys: ["↵"], label: "Expand" },
  { keys: ["Esc"], label: "Collapse" },
  { keys: ["J", "K"], label: "Move" },
  { keys: ["/"], label: "Search" },
];

/** The filter pills (refresh mock §4.13): Everything, then each group with its 7px glyph and its
 *  shared count; zero counts stay, drawn quiet. */
function filterOptions(items: readonly Pill[]): SegmentOption<Filter>[] {
  return items.map((p) => ({
    value: p.filter,
    label: p.label,
    mark: p.mark ? <StatusMark kind={p.mark} glyphOnly size={7} /> : undefined,
    count: p.count,
    you: p.filter === "needs_you",
  }));
}

function Right({ right }: { right: GroupRight }): ReactNode {
  if (right === null) return undefined;
  switch (right.kind) {
    case "stat":
      return right.parts.map((p, i) =>
        typeof p === "string" ? (
          <Fragment key={i}>{p}</Fragment>
        ) : "b" in p ? (
          <b key={i}>{p.b}</b>
        ) : (
          <span key={i} className={s.headAge}>
            <Age text={p.age} />
          </span>
        ),
      );
    case "note":
      return (
        <Inline as="span" gap={3} className={s.headNote}>
          <ArrowIcon />
          {right.text}
        </Inline>
      );
    case "shipped":
      return (
        <Badge tone="stat">
          <b>{`${String(right.n)} shipped`}</b> this week
        </Badge>
      );
  }
}

/** J / K move the keyboard to the next / previous row line in the list (never while typing). */
function useRowKeys(list: RefObject<HTMLDivElement | null>, enabled: boolean) {
  useDocumentKey(
    (e) => {
      if ((e.key !== "j" && e.key !== "k") || !isShortcut(e)) return;
      // Never while a dialog is open (New ticket, the peek) or from inside one (a confirm).
      if (dialogOpen() || document.activeElement?.closest("dialog, [role='dialog']")) return;

      const lines = [
        ...(list.current?.querySelectorAll<HTMLButtonElement>("[data-row-line]") ?? []),
      ].filter((el) => el.offsetParent !== null);
      if (lines.length === 0) return;

      const active = document.activeElement;
      const at = lines.findIndex((el) => el === active || el.parentElement?.contains(active));
      const next =
        at === -1
          ? e.key === "j"
            ? 0
            : lines.length - 1
          : Math.min(lines.length - 1, Math.max(0, at + (e.key === "j" ? 1 : -1)));
      e.preventDefault();
      lines[next]?.focus();
      lines[next]?.scrollIntoView({ block: "nearest" });
    },
    { enabled },
  );
}

export function CompactView() {
  const board = useBoard();
  const [params, setParams] = useSearchParams();
  const peek = usePeek();
  const now = useNow(AGE_TICK_MS);
  const filter = parseFilter(params.get(FILTER_PARAM));
  const repo = params.get(REPO_PARAM);

  const { query } = useSearchQuery();

  // The pills count the repo's whole board (they're the legend); the list shows what the filter
  // and the search (`?q=`) leave.
  const scoped = useMemo(() => inRepo(board.tickets, repo), [board.tickets, repo]);
  const listed = useMemo(() => bySearch(byFilter(scoped, filter), query), [scoped, filter, query]);
  const groups = useMemo(() => compactGroups(listed, board.me, now), [listed, board.me, now]);
  const options = useMemo(
    () => filterOptions(pills(scoped, board.me, filter)),
    [scoped, board.me, filter],
  );
  const foot = useMemo(() => footSummary(listed), [listed]);
  const listRef = useRef<HTMLDivElement>(null);

  // One row open at a time; groups the person flipped away from their default (Done collapsed).
  const [open, setOpen] = useState<string | null>(null);
  const [flipped, setFlipped] = useState<ReadonlySet<Attention>>(new Set());

  // Esc collapses the open row; while a peek is open, Esc is the peek's.
  const peekOpen = peek.key !== null;
  useDocumentKey(
    (e) => {
      if (e.key === "Escape") setOpen(null);
    },
    { enabled: open !== null && !peekOpen },
  );
  useRowKeys(listRef, !peekOpen);

  const toggleGroup = (id: Attention) => {
    setFlipped((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  };

  let body;
  if (board.isPending) body = <StatusLine inset>Loading</StatusLine>;
  else if (board.error)
    body = <StatusLine inset error>{`Can't reach the server · ${board.error.message}`}</StatusLine>;
  else if (board.tickets.length === 0) body = <NewTicketPrompt />;
  else if (groups.length === 0)
    body =
      query.length > 0 ? (
        <NoMatches where={filter !== "all" || repo ? "here" : undefined} />
      ) : (
        <EmptyState form="inset">{emptyText(filter, repo)}</EmptyState>
      );
  else
    body = compactSections(groups, filter === "all" && query.length === 0).map((sec) => {
      if (sec.kind === "empty")
        return (
          <EmptyState key={`empty-${sec.id}`} lead={sec.lead} className={s.emptyRow}>
            {sec.note}
          </EmptyState>
        );
      const g = sec.group;
      const collapsed = collapsedByDefault(g.id, filter) !== flipped.has(g.id);
      const bodyId = `cx-${g.id}`;
      return (
        <section key={g.id} className={s.group} aria-label={g.title}>
          <GroupHead
            name={g.title}
            count={g.items.length}
            glyph={g.mark}
            summary={g.right ? <Right right={g.right} /> : undefined}
            collapsed={collapsed}
            onToggle={() => {
              toggleGroup(g.id);
            }}
            controls={bodyId}
          />
          <DataRows id={bodyId} hidden={collapsed}>
            {g.items.map((t) => (
              <CompactRow
                key={t.key}
                ticket={t}
                open={open === t.key}
                now={now}
                onToggle={(key) => {
                  setOpen((cur) => (cur === key ? null : key));
                }}
              />
            ))}
          </DataRows>
        </section>
      );
    });

  return (
    <>
      <TopBar
        title="Compact"
        context={repo ? `Every ticket · ${repoShort(repo) || repo}` : "Every ticket"}
        middle={
          <SegmentedControl
            label="Filter"
            options={options}
            value={filter}
            onChange={(f) => {
              setParams((p) => withFilter(p, f));
            }}
          />
        }
      />
      <Surface className={s.surface}>
        <DataHead columns={COLUMNS} className={s.colhead} />
        <div ref={listRef} className={s.list}>
          {body}
        </div>
        <DataFoot hints={HINTS} hintClassName={s.keys} className={s.foot}>
          <Inline as="span" gap={3} className={s.sum}>
            <span>
              <b>{foot.tickets}</b> {plural(foot.tickets, "ticket")}
            </span>
            {foot.projects.length > 0 && (
              <>
                <span aria-hidden="true">·</span>
                {foot.projects.map((p) => (
                  <KeyChip key={p} size="sm">
                    {p}
                  </KeyChip>
                ))}
              </>
            )}
            <span aria-hidden="true">·</span>
            <span>
              <b>{foot.repos}</b> {plural(foot.repos, "repo")}
            </span>
          </Inline>
        </DataFoot>
      </Surface>
    </>
  );
}
