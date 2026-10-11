// The compact row (kit Kit.compactRow): one 36px line — mark · ref · stage · title · now · source ·
// people · upd. Click / Enter / Space on the line expands it in place (DESIGN §6); the expansion
// carries the row's actions, Peek and Open ticket. Server fields only.
import { useEffect, useId, useRef } from "react";

import type { Schemas } from "../../api/client";
import { stageModel } from "../../domain/stage";
import { statusKind } from "../../domain/status";
import { cx, Faces, RunMeter, StageGlyph, StatusMark } from "../../ui";
import { meterOfText } from "../../ui/meter";
import s from "./Compact.module.css";
import { Expanded } from "./Expanded";
import { nowCell, sourceText, updText } from "./model";

type Ticket = Schemas["TicketSummary"];

function NowCell({ ticket }: { ticket: Ticket }) {
  const cell = nowCell(ticket);
  if (cell.kind === "run") {
    const { meter } = cell;
    return (
      <span className={s.now}>
        <span className={s.runline}>
          <span className={s.agent}>{cell.actor}</span>
          <span className={s.step} title={cell.text}>
            {cell.text}
          </span>
          {meter && (
            <>
              <RunMeter k={meter.k} n={meter.n} state={meter.state} size="row" width={48} />
              <span className={s.of}>{meterOfText(meter.k, meter.n, meter.state)}</span>
            </>
          )}
        </span>
      </span>
    );
  }
  const { mark } = cell;
  return (
    <span className={s.now}>
      <StatusMark kind={mark.kind} name={mark.name} ask={mark.ask} />
      {cell.text && <span title={cell.text}>{cell.text}</span>}
    </span>
  );
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function CompactRow({
  ticket: t,
  open,
  now,
  onToggle,
}: {
  ticket: Ticket;
  open: boolean;
  now: number;
  onToggle: (key: string) => void;
}) {
  const moreId = useId();
  const ref = useRef<HTMLLIElement>(null);
  const kind = statusKind(t);

  // An opened row scrolls fully into view (12px top / 20px bottom margins via scroll-margin).
  useEffect(() => {
    if (open)
      ref.current?.scrollIntoView({
        block: "nearest",
        behavior: reducedMotion() ? "auto" : "smooth",
      });
  }, [open]);

  return (
    <li ref={ref} className={cx(s.crow, open && s.isOpen, kind === "done" && s.isDone)}>
      <button
        type="button"
        className={s.line}
        aria-expanded={open}
        aria-controls={open ? moreId : undefined}
        onClick={() => {
          onToggle(t.key);
        }}
      >
        {/* Needs-you rows leave the mark empty: the YOU form is in the Now column (KIT-GAP). */}
        <span className={s.mk}>{kind !== "you" && <StatusMark kind={kind} glyphOnly />}</span>
        <span className={s.ref}>{t.key}</span>
        <StageGlyph model={stageModel(t)} size="xs" className={s.stage} />
        <span className={s.ti} title={t.title}>
          {t.title}
        </span>
        <NowCell ticket={t} />
        <span className={s.repo}>{sourceText(t)}</span>
        {t.owner ? <Faces people={[{ id: t.owner.id, name: t.owner.display_name }]} /> : <span />}
        <span className={s.upd} title={new Date(t.attention_since).toLocaleString()}>
          {updText(t, now)}
        </span>
      </button>
      {open && <Expanded id={moreId} ticket={t} />}
    </li>
  );
}
