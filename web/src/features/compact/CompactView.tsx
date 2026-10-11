// Compact (DESIGN §6): every ticket, one 36px line each, grouped by attention, filtered by `?f=`
// (one group) and `?repo=` (one repo). The only list that shows the Inbox. A row expands in place,
// one at a time; Esc collapses it (when no peek is open). Every list and number comes from
// model.ts over useBoard(); this view only lays them out.
// Cut from the prototype: J/K/P/O keys, the runner pool gauge, FLIP glides, team bands (repos instead).
import { Fragment, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { Quiet } from "../../app/Quiet";
import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useBoard } from "../../app/useBoard";
import { repoShort } from "../../domain/format";
import type { Attention } from "../../domain/status";
import { GroupHead, Kbd } from "../../ui";
import { usePeek } from "../peek/usePeek";
import { NewTicketPrompt } from "../queue/QueueView";
import { useNow } from "../runs/useNow";
import s from "./Compact.module.css";
import {
  byFilter,
  collapsedByDefault,
  compactGroups,
  emptyText,
  FILTER_PARAM,
  footNote,
  inRepo,
  parseFilter,
  pills,
  REPO_PARAM,
  type Summary,
  withFilter,
} from "./model";
import { Pills } from "./Pills";
import { CompactRow } from "./Row";

/** Ages tick by the minute; nothing on a compact line shows seconds. */
const AGE_TICK_MS = 30_000;

const COLUMNS = ["", "Ref", "Stage", "Ticket", "Now", "Source", "People", "Upd"] as const;

function SummaryText({ parts }: { parts: Summary }) {
  return (
    <>
      {parts.map((p, i) =>
        typeof p === "string" ? <Fragment key={i}>{p}</Fragment> : <b key={i}>{p.b}</b>,
      )}
    </>
  );
}

export function CompactView() {
  const board = useBoard();
  const [params, setParams] = useSearchParams();
  const peek = usePeek();
  const now = useNow(AGE_TICK_MS);
  const filter = parseFilter(params.get(FILTER_PARAM));
  const repo = params.get(REPO_PARAM);

  const scoped = useMemo(() => inRepo(board.tickets, repo), [board.tickets, repo]);
  const listed = useMemo(() => byFilter(scoped, filter), [scoped, filter]);
  const groups = useMemo(() => compactGroups(listed, board.me, now), [listed, board.me, now]);
  const pillItems = useMemo(() => pills(scoped, board.me, filter), [scoped, board.me, filter]);

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
  else if (groups.length === 0) body = <p className={s.empty}>{emptyText(filter, repo)}</p>;
  else
    body = groups.map((g) => {
      const collapsed = collapsedByDefault(g.id, filter) !== flipped.has(g.id);
      const bodyId = `cx-${g.id}`;
      return (
        <section key={g.id} className={s.group} aria-label={g.title}>
          <GroupHead
            name={g.title}
            count={g.items.length}
            glyph={g.mark}
            summary={g.summary.length > 0 ? <SummaryText parts={g.summary} /> : undefined}
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
          {COLUMNS.map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </div>
        <div className={s.list}>{body}</div>
        <footer className={s.foot}>
          <span className={s.keys}>
            <Kbd>↵</Kbd> expand
          </span>
          <span className={s.keys}>
            <Kbd>Esc</Kbd> collapse
          </span>
          <span className={s.keys}>
            <Kbd>/</Kbd> search
          </span>
          <span className={s.grow} />
          <span className={s.note}>{footNote(listed)}</span>
        </footer>
      </Surface>
    </>
  );
}
