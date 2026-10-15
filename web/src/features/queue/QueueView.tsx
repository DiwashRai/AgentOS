// My queue (home), DESIGN §5 with our cuts (no Watching rack, pool gauge, freed-runner slots,
// NEW · PINNED or overflow chips) and only what is the viewer's: Needs you (incl. blocked tickets
// that are mine or nobody's), Agents at work, Ready to start (my inbox), Done recently (mine, last
// 7 days). The team's waiting and blocked tickets live in Compact. Every list and number comes
// from src/domain over useBoard()'s tickets, narrowed by the search (`?q=`); this view only lays
// them out. Click / Enter / Space on an item opens the peek (`?peek=KEY`).
import { type ReactNode, useMemo } from "react";
import { Link as RouterLink, useSearchParams } from "react-router";

import { Surface } from "../../app/Surface";
import { TopBar } from "../../app/TopBar";
import { useBoard } from "../../app/useBoard";
import { useSearchQuery } from "../../app/useSearchQuery";
import { counts, doneThisWeek, shippedThisWeek } from "../../domain/counts";
import { groups } from "../../domain/groups";
import { bySearch } from "../../domain/search";
import { statusWords, words } from "../../domain/words";
import {
  Badge,
  Button,
  DataRows,
  EmptyState,
  linkClass,
  StatusLine,
  StatusMark,
  ZoneHead,
} from "../../ui";
import { usePeek } from "../peek/usePeek";
import { withParam } from "../peek/params";
import { useNow } from "../../ui/useNow";
import { atWorkNote, CAP, compactHref, listsAnything, myRunning, rackGroups } from "./model";
import s from "./Queue.module.css";
import { NeedRow, QuietRow } from "./Rows";
import { Rack, RunTiles } from "./Runs";

export function NewTicketPrompt() {
  const [, setParams] = useSearchParams();
  return (
    <EmptyState
      form="line"
      action={
        <Button
          size="sm"
          onClick={() => {
            setParams((p) => withParam(p, "new", "1"));
          }}
        >
          {words.newTicket}
        </Button>
      }
    >
      Nothing here yet. Create a ticket.
    </EmptyState>
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
    <EmptyState
      form="line"
      action={
        <>
          <Button
            size="sm"
            onClick={() => {
              setQ("");
            }}
          >
            Clear search
          </Button>
          {children}
        </>
      }
    >
      {where ? `Nothing ${where} matches ${quoted}` : `No tickets match ${quoted}`}
    </EmptyState>
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
  const doneRecent = doneThisWeek(g.doneMine, now);

  let body;
  if (board.isPending) body = <StatusLine inset>Loading</StatusLine>;
  else if (board.error)
    body = <StatusLine inset error>{`Can't reach the server · ${board.error.message}`}</StatusLine>;
  else if (board.tickets.length === 0) body = <NewTicketPrompt />;
  else if (query.length > 0 && !listsAnything({ ...g, doneMine: doneRecent }, rack))
    body = (
      // Matches can sit where My queue never shows (the team's tickets, older done): point there.
      <NoMatches where={shown.length > 0 ? "in My queue" : undefined}>
        {shown.length > 0 && (
          <RouterLink className={linkClass} to={compactHref({ q })}>
            {`${String(shown.length)} in Compact`}
          </RouterLink>
        )}
      </NoMatches>
    );
  else {
    const shipped = shippedThisWeek(doneRecent, now);
    body = (
      <>
        {g.needYou.length === 0 ? (
          <EmptyState lead="All clear" className={s.emptyZone}>
            nothing needs you
          </EmptyState>
        ) : (
          <section aria-labelledby="q-need">
            <ZoneHead
              id="q-need"
              title={statusWords.you}
              count={g.needYou.length}
              note="Longest wait first"
              tone="you"
            />
            <DataRows as="div" gap={3} className={s.list}>
              {g.needYou.map((t) =>
                t.attention === "needs_you" ? (
                  <NeedRow key={t.key} ticket={t} me={me} now={now} onOpen={peek.open} />
                ) : (
                  <QuietRow key={t.key} ticket={t} me={me} now={now} onOpen={peek.open} />
                ),
              )}
            </DataRows>
          </section>
        )}

        {tiles.length > 0 || rack.length > 0 ? (
          <section aria-labelledby="q-work">
            <ZoneHead
              id="q-work"
              mark={<StatusMark kind="run" glyphOnly />}
              title={words.atWork}
              count={c.running}
              note={atWorkNote(c)}
            />
            <RunTiles tickets={tiles} now={now} onOpen={peek.open} />
            <Rack groups={rack} onOpen={peek.open} />
          </section>
        ) : (
          <EmptyState lead="All quiet" className={s.emptyZone}>
            nothing running, nothing queued
          </EmptyState>
        )}

        {g.readyMine.length > 0 && (
          <section aria-labelledby="q-ready">
            <ZoneHead
              id="q-ready"
              title={words.readyToStart}
              count={g.readyMine.length}
              note="Oldest first"
            />
            <DataRows as="div" gap={3} className={s.list}>
              {g.readyMine.map((t) => (
                <QuietRow key={t.key} ticket={t} me={me} now={now} onOpen={peek.open} />
              ))}
            </DataRows>
          </section>
        )}

        {doneRecent.length > 0 && (
          <section aria-labelledby="q-done">
            <ZoneHead
              id="q-done"
              title={words.doneRecent}
              count={doneRecent.length}
              note="Yours, last 7 days"
              tone="done"
              right={
                <>
                  {shipped > 0 && (
                    <Badge tone="stat">
                      <b>{`${String(shipped)} shipped`}</b>
                      <span className={s.statWeek}> this week</span>
                    </Badge>
                  )}
                  <RouterLink className={linkClass} to={compactHref({ f: "done", q })}>
                    All done tickets
                  </RouterLink>
                </>
              }
            />
            <DataRows as="div" gap={3} className={s.list}>
              {doneRecent.slice(0, CAP.done).map((t) => (
                <QuietRow key={t.key} ticket={t} me={me} now={now} onOpen={peek.open} />
              ))}
            </DataRows>
          </section>
        )}
      </>
    );
  }
  return (
    <>
      <TopBar
        title="My queue"
        context={me?.display_name}
        counters={["yourMove", "mine", "|", "running", "queued"]}
      />
      <Surface className={s.surface}>{body}</Surface>
    </>
  );
}
