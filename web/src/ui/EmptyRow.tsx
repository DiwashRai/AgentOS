import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./EmptyRow.module.css";

export interface EmptyRowProps {
  /** The lead word, in 600 ("All quiet"). */
  lead: string;
  /** The note after it ("nothing running, nothing queued"). */
  children?: ReactNode;
  className?: string | undefined;
}

/**
 * The dashed empty row (refresh mock §4.3): 34px, one line, a small dotted ring at the left. Only
 * the Needs you, Running and Queued zones use it; other empty groups stay hidden.
 */
export function EmptyRow({ lead, children, className }: EmptyRowProps) {
  return (
    <div className={cx(s.row, className)}>
      <i aria-hidden="true" />
      <b>{lead}</b>
      {children ? <span>{children}</span> : null}
    </div>
  );
}
