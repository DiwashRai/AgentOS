import type { ReactNode } from "react";
import s from "./Chip.module.css";
import { cx } from "./cx";

export interface ChipProps {
  children: ReactNode;
  /** Mono caps key before the text (e.g. a file kind). */
  k?: string | undefined;
  /** The dashed "+ Add context" chip. */
  add?: boolean | undefined;
  /** Renders a link when set, else a button. */
  href?: string | undefined;
  onClick?: (() => void) | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

export function Chip({ children, k, add = false, href, onClick, title, className }: ChipProps) {
  const cls = cx(s.chip, add && s.add, className);
  const body = (
    <>
      {k ? <span className={s.k}>{k}</span> : null}
      {children}
    </>
  );
  if (href !== undefined) {
    return (
      <a className={cls} href={href} title={title} onClick={onClick}>
        {body}
      </a>
    );
  }
  return (
    <button type="button" className={cls} title={title} onClick={onClick}>
      {body}
    </button>
  );
}
