// Fold: a native `details` under a chevron head, for content that only folds (a check's notes,
// "How each stage was called").
import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./Fold.module.css";
import { ChevronIcon } from "./icons";

export interface FoldProps {
  /** The summary's label. */
  summary: ReactNode;
  /** Quiet text after the label, led by "·" ("2 repairs"). */
  meta?: ReactNode;
  /** meta (default): 12/500 --ink-2. section: 13 --ink-1, for a fold that heads a section. */
  size?: "meta" | "section" | undefined;
  /** Called with the new state after the reader opens or closes it. */
  onToggle?: ((open: boolean) => void) | undefined;
  /** What the fold shows when open. */
  children?: ReactNode;
  className?: string | undefined;
}

export function Fold({ summary, meta, size = "meta", onToggle, children, className }: FoldProps) {
  return (
    <details
      className={cx(s.fold, className)}
      onToggle={
        onToggle &&
        ((e) => {
          onToggle(e.currentTarget.open);
        })
      }
    >
      <summary className={cx(s.head, size === "section" && s.section)}>
        <span className={s.chev}>
          <ChevronIcon />
        </span>
        {summary}
        {meta ? <span className={s.meta}>· {meta}</span> : null}
      </summary>
      {children}
    </details>
  );
}
