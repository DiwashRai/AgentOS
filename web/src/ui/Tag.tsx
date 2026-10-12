import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./Tag.module.css";

export interface TagProps {
  /** Text, optionally led by an icon (TypeIcon, PriorityIcon). Rendered as written: sentence case. */
  children: ReactNode;
  /** Priority tag: text in the blocked colour. */
  prio?: boolean | undefined;
  /** Quiet tag (the repo in the kicker): --ink-2, 400. */
  quiet?: boolean | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

export function Tag({ children, prio = false, quiet = false, title, className }: TagProps) {
  return (
    <span className={cx(s.tag, prio && s.prio, quiet && s.quiet, className)} title={title}>
      {children}
    </span>
  );
}
