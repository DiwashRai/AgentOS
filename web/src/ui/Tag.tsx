import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./Tag.module.css";

export interface TagProps {
  children: ReactNode;
  /** Priority tag: text in the blocked colour. */
  prio?: boolean | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

export function Tag({ children, prio = false, title, className }: TagProps) {
  return (
    <span className={cx(s.tag, prio && s.prio, className)} title={title}>
      {children}
    </span>
  );
}
