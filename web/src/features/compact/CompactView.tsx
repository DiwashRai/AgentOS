// Compact (DESIGN §6, refresh HANDOFF §5): every ticket, one line each, grouped by attention,
// filtered by `?f=` (one group), `?repo=` (one repo) and the search `?q=`. The only list that shows
// the Inbox. A row expands in place, one at a time; Esc collapses it (when no peek is open); J/K
// move the keyboard between rows. Every list and number comes from model.ts over useBoard(); this
// view only lays them out.
// Cut from the prototype: P/O keys, the runner pool gauge, FLIP glides, team bands (repos instead).
import {
  Fragment,
  type ReactNode,
  type RefObject,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router";

import { isShortcut } from "../../app/keys";
import { Quiet } from "../../app/Quiet";
import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useBoard } from "../../app/useBoard";
import { useSearchQuery } from "../../app/useSearchQuery";
import { repoShort } from "../../domain/format";
import { bySearch } from "../../domain/search";
import type { Attention } from "../../domain/status";
import { Age, ArrowIcon, EmptyRow, GroupHead, Kbd, KeyChip, TickBadge } from "../../ui";
import { usePeek } from "../peek/usePeek";
import { NewTicketPrompt, NoMatches } from "../queue/QueueView";
import { useNow } from "../runs/useNow";
import s from "./Compact.module.css";
import {
  byFilter,
  collapsedByDefault,
  compactGroups,
  compactSections,
  emptyText,
  FILTER_PARAM,
  footSummary,
  type GroupRight,
  inRepo,
  parseFilter,
  pills,
  plural,
  REPO_PARAM,
  withFilter,
} from "./model";
import { Pills } from "./Pills";
import { CompactRow } from "./Row";

/** Ages tick by the minute; nothing on a compact line shows seconds. */
const AGE_TICK_MS = 30_000;

/** Column heads, with the class that hides a column on a narrow surface. */
const COLUMNS: readonly [string, string | undefined][] = [
  ["Ref", undefined],
  ["Stage", s.cStage],
  ["Ticket", undefined],
  ["Now", s.cNow],
  ["Source", s.cSrc],
  ["People", s.cPeople],
  ["Upd", undefined],
];

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
        <span className={s.headNote}>
          <ArrowIcon />
          {right.text}
        </span>
      );
    case "shipped":
      return (
        <>
          <TickBadge size={12} />
          <span>
            <b>{`${String(right.n)} shipped`}</b> this week
          </span>
        </>
      );
  }
}

/** J / K move the keyboard to the next / previous row line in the list (never while typing). */
function useRowKeys(list: RefObject<HTMLDivElement | null>, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.key !== "j" && e.key !== "k") || !isShortcut(e)) return;
      // Never from inside a dialog (New ticket, a confirm).
      if (document.activeElement?.closest("dialog, [role='dialog']")) return;
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
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [list, enabled]);
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
  const pillItems = useMemo(() => pills(scoped, board.me, filter), [scoped, board.me, filter]);
  const foot = useMemo(() => footSummary(listed), [listed]);
  const listRef = useRef<HTMLDivElement>(null);

  // One row open at a time; groups the person flipped away from their default (Done collapsed).
  const [open, setOpen] = useState<string | null>(null);
  const [flipped, setFlipped] = useState<ReadonlySet<Attention>>(new Set());

  // Esc collapses the open row; while a peek is open, Esc is the peek's.
  const peekOpen = peek.key !== null;
  useEffect(() => {
    if (open === null || peekOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [open, peekOpen]);
  useRowKeys(listRef, !peekOpen);

  const toggleGroup = (id: Attention) => {
    setFlipped((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  };

  let body;
  if (board.isPending) body = <Quiet>Loading</Quiet>;
  else if (board.error)
    body = <Quiet error>{`Can't reach the server · ${board.error.message}`}</Quiet>;
  else if (board.tickets.length === 0) body = <NewTicketPrompt />;
  else if (groups.length === 0)
    body =
      query.length > 0 ? (
        <NoMatches where={filter !== "all" || repo ? "here" : undefined} />
      ) : (
        <p className={s.empty}>{emptyText(filter, repo)}</p>
      );
  else
    body = compactSections(groups, filter === "all" && query.length === 0).map((sec) => {
      if (sec.kind === "empty")
        return (
          <EmptyRow key={`empty-${sec.id}`} lead={sec.lead} className={s.emptyRow}>
            {sec.note}
          </EmptyRow>
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
          <ul id={bodyId} className={s.rows} hidden={collapsed}>
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
          </ul>
        </section>
      );
    });

  return (
    <>
      <TopBar
        title="Compact"
        context={repo ? `Every ticket · ${repoShort(repo) || repo}` : "Every ticket"}
        middle={
          <Pills
            items={pillItems}
            active={filter}
            onPick={(f) => {
              setParams((p) => withFilter(p, f));
            }}
          />
        }
      />
      <Surface className={s.surface}>
        <div className={s.colhead} aria-hidden="true">
          {COLUMNS.map(([label, cls]) => (
            <span key={label} className={cls}>
              {label}
            </span>
          ))}
        </div>
        <div ref={listRef} className={s.list}>
          {body}
        </div>
        <footer className={s.foot}>
          <span className={s.keys}>
            <Kbd>↵</Kbd>Expand
          </span>
          <span className={s.keys}>
            <Kbd>Esc</Kbd>Collapse
          </span>
          <span className={s.keys}>
            <span className={s.caps}>
              <Kbd>J</Kbd>
              <Kbd>K</Kbd>
            </span>
            Move
          </span>
          <span className={s.keys}>
            <Kbd>/</Kbd>Search
          </span>
          <span className={s.grow} />
          <span className={s.sum}>
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
          </span>
        </footer>
      </Surface>
    </>
  );
}
