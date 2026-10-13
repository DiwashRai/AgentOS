// The compact row (refresh HANDOFF §5 "Compact row"): one 42px raised line (38px flat when settled)
// with a 3px status edge — ref · stage · ticket · now · source · people · upd. Click / Enter / Space
// on the line expands it in place (DESIGN §6); the expansion carries the row's actions, Peek and
// Open ticket. Hover and focus light the row up and never move it. An Inbox row reveals one action,
// Add context, over its empty Now cell: a sibling of the line (never inside the line's <button>), so
// Tab from the focused line reaches it. A ticket the server won't dispatch yet (`dispatch_block`)
// says why in its Now cell, in the you hue. Server fields only.
import { useEffect, useId, useRef } from "react";
import { useNavigate } from "react-router";

import type { Schemas } from "../../api/client";
import { stageModel } from "../../domain/stage";
import {
  Age,
  Avatar,
  AvatarSlot,
  Button,
  cx,
  KeyChip,
  PriorityIcon,
  RunMeter,
  StageGlyph,
  StatusMark,
  TypeIcon,
} from "../../ui";
import { meterOfText } from "../../ui/meter";
import { ticketPath } from "../ticket/model";
import s from "./Compact.module.css";
import { Expanded } from "./Expanded";
import { type Edge, edgeOf, isFlat, nowCell, sourceText, updText } from "./model";

type Ticket = Schemas["TicketSummary"];

const EDGE_CLASS: Record<Edge, string | undefined> = {
  you: s.edgeYou,
  live: s.edgeLive,
  blocked: s.edgeBlocked,
  done: s.edgeDone,
  quiet: s.edgeQuiet,
};

function NowCell({ ticket }: { ticket: Ticket }) {
  const cell = nowCell(ticket);
  switch (cell.kind) {
    case "run": {
      const { meter } = cell;
      return (
        <span className={cx(s.now, s.cNow)}>
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
    case "outcome":
      return (
        <span className={cx(s.now, s.cNow)}>
          <span title={[cell.word, cell.text].filter(Boolean).join(" · ")}>
            <span className={cx(cell.outcome === "abandoned" && s.closed)}>{cell.word}</span>
            {cell.text ? ` · ${cell.text}` : null}
          </span>
        </span>
      );
    case "empty":
      return <span className={cx(s.now, s.cNow)} />;
    case "block":
      return (
        <span className={cx(s.now, s.cNow, s.nowBlock)}>
          <span className={s.flag} title={cell.text}>
            {cell.text}
          </span>
        </span>
      );
    case "mark": {
      const { mark } = cell;
      return (
        <span className={cx(s.now, s.cNow)}>
          <StatusMark kind={mark.kind} name={mark.name} ask={mark.ask} />
          {cell.text && (
            <span className={cx(cell.flag && s.flag)} title={cell.text}>
              {cell.text}
            </span>
          )}
        </span>
      );
    }
  }
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
  const navigate = useNavigate();
  const inbox = t.attention === "inbox";

  // An opened row scrolls fully into view (12px top / 20px bottom margins via scroll-margin).
  useEffect(() => {
    if (open)
      ref.current?.scrollIntoView({
        block: "nearest",
        behavior: reducedMotion() ? "auto" : "smooth",
      });
  }, [open]);

  return (
    <li
      ref={ref}
      className={cx(
        s.crow,
        EDGE_CLASS[edgeOf(t)],
        isFlat(t) ? s.isFlat : s.isRaised,
        t.attention === "needs_you" && s.isYou,
        inbox && s.isInbox,
        open && s.isOpen,
      )}
    >
      <button
        type="button"
        className={s.line}
        data-row-line=""
        aria-expanded={open}
        aria-controls={open ? moreId : undefined}
        onClick={() => {
          onToggle(t.key);
        }}
      >
        <span className={s.cRef}>
          <KeyChip>{t.key}</KeyChip>
        </span>
        <StageGlyph model={stageModel(t)} className={cx(s.stage, s.cStage)} />
        <span className={s.ti}>
          <TypeIcon type={t.type} />
          <PriorityIcon priority={t.priority} />
          <span title={t.title}>{t.title}</span>
        </span>
        <NowCell ticket={t} />
        <span className={cx(s.src, s.cSrc)}>{sourceText(t)}</span>
        <span className={cx(s.people, s.cPeople)}>
          {t.owner ? <Avatar name={t.owner.display_name} id={t.owner.id} /> : <AvatarSlot />}
        </span>
        <span className={s.upd} title={new Date(t.attention_since).toLocaleString()}>
          <Age text={updText(t, now)} />
        </span>
      </button>
      {inbox && (
        // Lies over the line on the same grid, so the button lands in the Now column.
        <span className={s.overlay}>
          <span className={s.addCtx}>
            <Button
              variant="outline"
              size="xs"
              onClick={() => {
                void navigate(ticketPath(t.key, "context"));
              }}
            >
              Add context
            </Button>
          </span>
        </span>
      )}
      {open && <Expanded id={moreId} ticket={t} />}
    </li>
  );
}
