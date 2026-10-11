// My queue (home), DESIGN §5 with our cuts (no Watching rack, pool gauge, freed-runner slots,
// NEW · PINNED or overflow chips). Every list and number comes from src/domain over useBoard()'s
// tickets, narrowed by the search (`?q=`); this view only lays them out. Click / Enter / Space on
// an item opens the peek (`?peek=KEY`).
import { type ReactNode, useMemo } from "react";
import { Link as RouterLink, useSearchParams } from "react-router";

import { Quiet } from "../../app/Quiet";
import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useBoard } from "../../app/useBoard";
import { useSearchQuery } from "../../app/useSearchQuery";
import { counts } from "../../domain/counts";
import { groups } from "../../domain/groups";
import { bySearch } from "../../domain/search";
import { statusWords, words } from "../../domain/words";
import { Button, linkClass, StatusMark, ZoneHead } from "../../ui";
import { usePeek } from "../peek/usePeek";
import { withParam } from "../peek/params";
import { useNow } from "../runs/useNow";
import {
  atWorkNote,
  CAP,
  compactHref,
  listsAnything,
  myRunning,
  rackGroups,
  showAllHref,
  SHOW_ALL_FILTER,
} from "./model";
import s from "./Queue.module.css";
import { NeedRow, QuietRow } from "./Rows";
import { Rack, RunTiles } from "./Runs";

function ShowAll({
  group,
  total,
  q,
}: {
  group: keyof typeof SHOW_ALL_FILTER;
  total: number;
  q: string;
}) {
  return (
    <RouterLink className={linkClass} to={showAllHref(group, q)}>
      {`Show all ${String(total)}`}
    </RouterLink>
  );
}

export function NewTicketPrompt() {
  const [, setParams] = useSearchParams();
  return (
    <div className={s.nothing}>
      <p className={s.empty}>Nothing here yet. Create a ticket.</p>
      <Button
        size="sm"
        onClick={() => {
          setParams((p) => withParam(p, "new", "1"));
        }}
      >
        {words.newTicket}
      </Button>
    </div>
  );
}

/** The empty state while the search (`?q=`) hides every ticket: a way out, and `children` for
 *  any other (e.g. matches that live outside this view). */
export function NoMatches({
  where,
  children,
}: {
  /** Narrows the line: "Nothing in My queue matches …" instead of "No tickets match …". */
  where?: string | undefined;
  children?: ReactNode;
}) {
  const { q, setQ } = useSearchQuery();
  const quoted = `“${q.trim()}”`;
  return (
    <div className={s.nothing}>
      <p className={s.empty}>
        {where ? `Nothing ${where} matches ${quoted}` : `No tickets match ${quoted}`}
      </p>
      <Button
        size="sm"
        onClick={() => {
          setQ("");
        }}
      >
        Clear search
      </Button>
      {children}
    </div>
  );
}

export function QueueView() {
  const board = useBoard();
  const { me } = board;
  const { q, query } = useSearchQuery();
  // The lists and their heads show what the search leaves; the top-bar counters keep counting
  // the whole board (they're the legend).
  const shown = useMemo(() => bySearch(board.tickets, query), [board.tickets, query]);
  const g = useMemo(() => groups(shown, me), [shown, me]);
  const c = useMemo(() => counts(shown, me), [shown, me]);
  const peek = usePeek();
  const now = useNow(1000);
  const tiles = useMemo(() => myRunning(g.atWork), [g.atWork]);
  const rack = useMemo(() => rackGroups(shown, g.atWork, me), [shown, g.atWork, me]);

  let body;
  if (board.isPending) body = <Quiet>Loading</Quiet>;
  else if (board.error)
    body = <Quiet error>{`Can't reach the server · ${board.error.message}`}</Quiet>;
  else if (board.tickets.length === 0) body = <NewTicketPrompt />;
  else if (query.length > 0 && !listsAnything(g, rack))
    body = (
      // Matches can sit where My queue never shows (the inbox, the team's waits): point there.
      <NoMatches where={shown.length > 0 ? "in My queue" : undefined}>
        {shown.length > 0 && (
          <RouterLink className={linkClass} to={compactHref({ q })}>
            {`${String(shown.length)} in Compact`}
          </RouterLink>
        )}
      </NoMatches>
    );
  else {
    const needShown = g.needYou.slice(0, CAP.needYou);
    const needMore = g.needYou.length - needShown.length;
    const quiet = [
      {
        group: "waitingMine",
        title: statusWords.wait,
        items: g.waitingMine,
        count: c.waitingMine,
        note: undefined,
      },
      {
        group: "blocked",
        title: statusWords.blk,
        items: g.blocked,
        count: c.blocked,
        note: "yours first",
      },
      {
        group: "doneRecent",
        title: words.doneRecent,
        items: g.doneRecent,
        count: c.done,
        note: undefined,
      },
    ] as const;
    body = (
      <>
        <section aria-labelledby="q-need">
          <ZoneHead
            id="q-need"
            title={statusWords.you}
            count={c.needYou}
            note="Longest wait first"
          />
          {needShown.length === 0 ? (
            <p className={s.empty}>Nothing needs you.</p>
          ) : (
            <div className={s.list}>
              {needShown.map((t) => (
                <NeedRow key={t.key} ticket={t} me={me} now={now} onOpen={peek.open} />
              ))}
            </div>
          )}
          {needMore > 0 && (
            <div className={s.overflow}>
              <b>{`+${String(needMore)} more`}</b>
              <ShowAll group="needYou" total={g.needYou.length} q={q} />
            </div>
          )}
        </section>

        {(tiles.length > 0 || rack.length > 0) && (
          <section aria-labelledby="q-work">
            <ZoneHead
              id="q-work"
              mark={<StatusMark kind="run" glyphOnly />}
              title={words.atWork}
              count={c.running}
              note={atWorkNote(c)}
            />
            <RunTiles tickets={tiles} now={now} onOpen={peek.open} />
          </section>
        )}
        <Rack groups={rack} onOpen={peek.open} />

        <div className={s.bottom}>
          {quiet.map(
            ({ group, title, items, count, note }) =>
              items.length > 0 && (
                <section key={group} aria-labelledby={`q-${group}`}>
                  <ZoneHead
                    id={`q-${group}`}
                    title={title}
                    count={count}
                    note={note}
                    right={
                      items.length > CAP.quiet ? (
                        <ShowAll group={group} total={items.length} q={q} />
                      ) : undefined
                    }
                  />
                  <div className={s.list}>
                    {items.slice(0, CAP.quiet).map((t) => (
                      <QuietRow key={t.key} ticket={t} me={me} now={now} onOpen={peek.open} />
                    ))}
                  </div>
                </section>
              ),
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <TopBar
        title="My queue"
        context={me?.display_name}
        counters={["needYou", "blockedMine", "mine", "|", "running", "queued"]}
      />
      <Surface className={s.surface}>{body}</Surface>
    </>
  );
}
