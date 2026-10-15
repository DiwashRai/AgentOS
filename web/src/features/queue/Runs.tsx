// Agents at work: my running runs as tiles, and the rack (my queued runs + the team's other runs
// by repo) as one-line rack lines. Click / Enter / Space opens the peek.
import type { CSSProperties } from "react";

import type { Schemas } from "../../api/client";
import { stageModel } from "../../domain/stage";
import { statusKind } from "../../domain/status";
import { meterOf, runLine } from "../../domain/ticketView";
import { onlineWords } from "../../domain/words";
import {
  Avatar,
  cx,
  GroupHead,
  Inline,
  KeyChip,
  RunMeter,
  Stack,
  StageGlyph,
  StatusMark,
  text,
} from "../../ui";
import { peekItemProps } from "../peek/usePeek";
import { CAP, type RackGroup, rackColumns } from "./model";
import s from "./Queue.module.css";

type Ticket = Schemas["TicketSummary"];

/** A quiet number at the right of its cell ("3/5", the elapsed time). */
const num = cx(text.meta, s.num);

export function RunTile({
  ticket: t,
  now,
  onOpen,
}: {
  ticket: Ticket;
  now: number;
  onOpen: (key: string) => void;
}) {
  const line = runLine(t, now);
  const { meter } = line;
  return (
    <Stack
      gap={3}
      className={s.tile}
      {...peekItemProps(`${t.key} ${t.title}`, () => {
        onOpen(t.key);
      })}
    >
      <Inline gap={3} className={s.tileHd}>
        <StatusMark kind="run" name={line.label} />
        {line.elapsed !== null && <span className={cx(num, s.tileEl)}>{line.elapsed}</span>}
      </Inline>
      <span className={s.tileTtl} title={t.title}>
        {t.title}
      </span>
      <Inline gap={4} className={s.tileMeta}>
        <KeyChip>{t.key}</KeyChip>
        <StageGlyph model={stageModel(t)} size="xs" />
      </Inline>
      <Inline gap={4} align="baseline" className={s.tileNow}>
        <span className={cx(s.grow, text.ellipsis)} title={t.now.text}>
          {line.step}
        </span>
        {meter && <span className={num}>{meter.text}</span>}
      </Inline>
      {meter && <RunMeter k={meter.k} n={meter.n} state={meter.state} size="tile" />}
    </Stack>
  );
}

export function RunTiles({
  tickets,
  now,
  onOpen,
}: {
  tickets: Ticket[];
  now: number;
  onOpen: (key: string) => void;
}) {
  if (tickets.length === 0) return null;
  return (
    // Always CAP.tiles columns, so one run is one tile wide rather than the whole row.
    <div className={s.tiles} style={{ "--n": CAP.tiles } as CSSProperties}>
      {tickets.map((t) => (
        <RunTile key={t.key} ticket={t} now={now} onOpen={onOpen} />
      ))}
    </div>
  );
}

function RackLine({ ticket: t, onOpen }: { ticket: Ticket; onOpen: (key: string) => void }) {
  const kind = statusKind(t) === "queued" ? "queued" : "run";
  const meter = meterOf(t);
  return (
    <div
      className={cx(s.rl, kind === "queued" && s.isQueued)}
      title={t.title}
      {...peekItemProps(`${t.key} ${t.title}`, () => {
        onOpen(t.key);
      })}
    >
      <Inline as="span">
        <StatusMark kind={kind} glyphOnly />
      </Inline>
      <Inline as="span">
        <KeyChip size="sm">{t.key}</KeyChip>
      </Inline>
      <span className={cx(s.rlT, text.ellipsis)}>{t.title}</span>
      {meter ? (
        <RunMeter k={meter.k} n={meter.n} state={meter.state} size="row" width={36} />
      ) : (
        <span />
      )}
      <span className={num}>{meter ? meter.text : ""}</span>
      {t.owner ? <Avatar name={t.owner.display_name} id={t.owner.id} /> : <span />}
    </div>
  );
}

export function Rack({
  groups,
  onOpen,
}: {
  groups: RackGroup<Ticket>[];
  onOpen: (key: string) => void;
}) {
  if (groups.length === 0) return null;
  return (
    <div className={s.rack}>
      {rackColumns(groups, 3).map((col, i) => (
        <Stack key={i} gap={5}>
          {col.map((g) => (
            <section key={g.id} aria-label={g.name}>
              <GroupHead
                name={g.name}
                size="sm"
                note={g.online ? <span className={s.online}>{onlineWords[g.online]}</span> : null}
                count={g.items.length}
              />
              {g.items.map((t) => (
                <RackLine key={t.key} ticket={t} onOpen={onOpen} />
              ))}
            </section>
          ))}
        </Stack>
      ))}
    </div>
  );
}
