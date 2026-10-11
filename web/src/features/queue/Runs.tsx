// Agents at work: my running runs as tiles, and the rack (my queued runs + the team's other runs
// by repo) as one-line rack lines. Click / Enter / Space opens the peek.
import type { CSSProperties } from "react";

import type { Schemas } from "../../api/client";
import { elapsed, srcLabel } from "../../domain/format";
import { planText } from "../../domain/plan";
import { stageModel } from "../../domain/stage";
import { statusKind } from "../../domain/status";
import { Avatar, cx, RunMeter, StageGlyph, StatusMark } from "../../ui";
import { meterOfText } from "../../ui/meter";
import { peekItemProps } from "../peek/usePeek";
import { CAP, meterOf, type RackGroup, rackColumns, runName, runSeconds } from "./model";
import s from "./Queue.module.css";

type Ticket = Schemas["TicketSummary"];

export function RunTile({
  ticket: t,
  now,
  onOpen,
}: {
  ticket: Ticket;
  now: number;
  onOpen: (key: string) => void;
}) {
  const meter = meterOf(t);
  const secs = runSeconds(t, now);
  return (
    <article
      className={cx(s.tile, s.lift)}
      {...peekItemProps(t.title, () => {
        onOpen(t.key);
      })}
    >
      <div className={s.tileHd}>
        <StatusMark kind="run" name={runName(t)} />
        {secs !== null && <span className={s.tileEl}>{elapsed(secs)}</span>}
      </div>
      <span className={s.tileTtl} title={t.title}>
        {t.title}
      </span>
      <div className={s.tileMeta}>
        <span className={s.src}>{srcLabel(t)}</span>
        <StageGlyph model={stageModel(t)} size="xs" />
      </div>
      <div className={s.tileNow}>
        <span title={t.now.text}>{planText(t.now.text)}</span>
        {meter && <span className={s.of}>{meterOfText(meter.k, meter.n, meter.state)}</span>}
      </div>
      {meter && <RunMeter k={meter.k} n={meter.n} state={meter.state} size="tile" />}
    </article>
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
      {...peekItemProps(t.title, () => {
        onOpen(t.key);
      })}
    >
      <span className={s.rlMark}>
        <StatusMark kind={kind} glyphOnly />
      </span>
      <span className={s.src}>{srcLabel(t)}</span>
      <span className={s.rlT}>{t.title}</span>
      {meter ? (
        <RunMeter k={meter.k} n={meter.n} state={meter.state} size="row" width={36} />
      ) : (
        <span />
      )}
      <span className={s.of}>{meter ? meterOfText(meter.k, meter.n, meter.state) : ""}</span>
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
        <div key={i}>
          {col.map((g) => (
            <section key={g.id} className={s.rackGroup} aria-label={g.name}>
              <div className={s.gh}>
                <span>{g.name}</span>
                <span className={s.ghCount}>{g.items.length}</span>
                <span className={s.ghRule} aria-hidden="true" />
              </div>
              {g.items.map((t) => (
                <RackLine key={t.key} ticket={t} onOpen={onOpen} />
              ))}
            </section>
          ))}
        </div>
      ))}
    </div>
  );
}
