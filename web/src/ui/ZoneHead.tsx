import type { ReactNode } from "react";
import { CountPill } from "./CountPill";
import { cx } from "./cx";
import s from "./ZoneHead.module.css";

export interface ZoneHeadProps {
  title: string;
  count?: number | undefined;
  /** Optional leading mark, usually a `<StatusMark … />`. */
  mark?: ReactNode;
  note?: string | undefined;
  /** Right slot after the hairline (a stat, links, keys). `<b>` inside is emphasised. */
  right?: ReactNode;
  /** you = title + count in the you blue (Needs you); done = in green (Done recently). */
  tone?: "neutral" | "you" | "done" | undefined;
  /** id for the h2, so a section can be `aria-labelledby` it. */
  id?: string | undefined;
  className?: string | undefined;
}

/** The My queue zone head (refresh HANDOFF §5 "Zone head"): 24px, title 15/600, count pill,
 *  note 12 `--ink-3`, hairline, right slot. Zones sit 18px apart (`--sp-zone`; the view's). */
export function ZoneHead({
  title,
  count,
  mark,
  note,
  right,
  tone = "neutral",
  id,
  className,
}: ZoneHeadProps) {
  return (
    <div className={cx(s.zh, tone !== "neutral" && s[tone], className)}>
      {mark ? <span className={s.mark}>{mark}</span> : null}
      <h2 id={id}>{title}</h2>
      {count !== undefined ? <CountPill count={count} tone={tone} /> : null}
      {note ? <span className={s.note}>{note}</span> : null}
      <span className={s.rule} aria-hidden="true" />
      {right ? <span className={s.right}>{right}</span> : null}
    </div>
  );
}
