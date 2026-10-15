import type { ReactNode } from "react";
import { CountPill } from "./CountPill";
import { cx } from "./cx";
import s from "./GroupHead.module.css";
import { ChevronIcon } from "./icons";
import { StatusMark, type MarkKind } from "./StatusMark";

export interface GroupHeadProps {
  name: string;
  count?: number | undefined;
  /** Status glyph in the 14px icon box (the small mark; done is the tick badge). */
  glyph?: MarkKind | undefined;
  /** Any other icon for the 14px box (a sm KeyChip for an Intake query). Wins over `glyph`. */
  icon?: ReactNode;
  /** you = name + count in the you blue; done = in green. Default: from `glyph` (you / done). */
  tone?: "neutral" | "you" | "done" | undefined;
  /** A quiet word right after the name (a Badge, e.g. a client's "Offline"). */
  note?: ReactNode;
  /** The right slot after the hairline: a stat, a JQL (the view gives it mono), the Inbox note.
   *  `<b>` inside is emphasised. */
  summary?: ReactNode;
  /** md (default): 34px, name 13.5/600 in --ink-1. sm: 32px, name 12.5/600 in --ink-2. */
  size?: "md" | "sm" | undefined;
  /** Whether the group body is hidden. Only for a collapsible head. */
  collapsed?: boolean | undefined;
  /** Toggles the group. Without it the head is static: no chevron, not a button. */
  onToggle?: (() => void) | undefined;
  /** id of the group body this head controls. */
  controls?: string | undefined;
  className?: string | undefined;
}

/**
 * The group head of Compact and Intake (collapsible) and of My queue's rack (static; refresh
 * HANDOFF §5 "Group head"): 34px, chevron 10 · icon 14 · name 13.5/600 · count pill · hairline ·
 * right slot. A static head drops the chevron and the button, and ellipsizes its name. Spacing
 * above it (12px; 0 after a column head) is the view's.
 */
export function GroupHead({
  name,
  count,
  glyph,
  icon,
  tone,
  note,
  summary,
  size = "md",
  collapsed = false,
  onToggle,
  controls,
  className,
}: GroupHeadProps) {
  const t = tone ?? (glyph === "you" ? "you" : glyph === "done" ? "done" : "neutral");
  const lead = icon ?? (glyph ? <StatusMark kind={glyph} glyphOnly size={7} /> : null);
  const cls = cx(
    s.gh,
    t !== "neutral" && s[t],
    size === "sm" && s.sm,
    !onToggle && s.static,
    className,
  );

  const parts = (
    <>
      {lead ? (
        <span className={s.icon} aria-hidden="true">
          {lead}
        </span>
      ) : null}
      <span className={s.name}>{name}</span>
      {note}
      {count !== undefined ? <CountPill count={count} tone={t} /> : null}
      <span className={s.r} aria-hidden="true" />
      {summary ? <span className={s.s}>{summary}</span> : null}
    </>
  );

  if (!onToggle) return <div className={cls}>{parts}</div>;

  return (
    <button
      type="button"
      className={cls}
      aria-expanded={!collapsed}
      aria-controls={controls}
      onClick={onToggle}
    >
      <span className={s.chev}>
        <ChevronIcon />
      </span>
      {parts}
    </button>
  );
}
