import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./ZoneHead.module.css";

export interface ZoneHeadProps {
  title: string;
  count?: number | undefined;
  /** Optional leading mark, usually a `<StatusMark … />`. */
  mark?: ReactNode;
  note?: string | undefined;
  /** Right slot after the hairline (links, keys). */
  right?: ReactNode;
  /** id for the h2, so a section can be `aria-labelledby` it. */
  id?: string | undefined;
  className?: string | undefined;
}

export function ZoneHead({ title, count, mark, note, right, id, className }: ZoneHeadProps) {
  return (
    <div className={cx(s.zh, className)}>
      {mark ? <span className={s.mark}>{mark}</span> : null}
      <h2 id={id}>{title}</h2>
      {count !== undefined ? <span className={s.count}>{count}</span> : null}
      {note ? <span className={s.note}>{note}</span> : null}
      <span className={s.rule} aria-hidden="true" />
      {right ? <span className={s.right}>{right}</span> : null}
    </div>
  );
}
