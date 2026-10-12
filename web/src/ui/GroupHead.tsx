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
  /** The right slot after the hairline: a stat, a JQL (the view gives it mono), the Inbox note.
   *  `<b>` inside is emphasised. */
  summary?: ReactNode;
  collapsed: boolean;
  onToggle: () => void;
  /** id of the group body this head controls. */
  controls?: string | undefined;
  className?: string | undefined;
}

/**
 * The collapsible group head of Compact and Intake (refresh HANDOFF §5 "Group head"): 34px,
 * chevron 10 · icon 14 · name 13.5/600 · count pill · hairline · right slot. Spacing above it
 * (12px; 0 after a column head) is the view's.
 */
export function GroupHead({
  name,
  count,
  glyph,
  icon,
  tone,
  summary,
  collapsed,
  onToggle,
  controls,
  className,
}: GroupHeadProps) {
  const t = tone ?? (glyph === "you" ? "you" : glyph === "done" ? "done" : "neutral");
  const lead = icon ?? (glyph ? <StatusMark kind={glyph} glyphOnly size={7} /> : null);
  return (
    <button
      type="button"
      className={cx(s.gh, t !== "neutral" && s[t], className)}
      aria-expanded={!collapsed}
      aria-controls={controls}
      onClick={onToggle}
    >
      <span className={s.chev}>
        <ChevronIcon />
      </span>
      {lead ? (
        <span className={s.icon} aria-hidden="true">
          {lead}
        </span>
      ) : null}
      <span className={s.name}>{name}</span>
      {count !== undefined ? <CountPill count={count} tone={t} /> : null}
      <span className={s.r} aria-hidden="true" />
      {summary ? <span className={s.s}>{summary}</span> : null}
    </button>
  );
}
