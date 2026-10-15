// Badge: a head's small figures.
//   counter  a top-bar count: the number, then the caption; zero recedes, `you` is the tinted pill
//   stat     a head's statistic led by the tick: "✓ 2 shipped this week"
// counter carries `data-zero` (and `data-you` while tinted), so a view's container queries can
// hide it without restating the look.
import type { ReactNode } from "react";
import s from "./Badge.module.css";
import { cx } from "./cx";
import { TickBadge } from "./TickBadge";

export interface BadgeProps {
  tone: "counter" | "stat";
  /** The text. counter: the caption after the number. */
  children?: ReactNode;
  /** counter: the number. Zero draws quiet. */
  count?: number | undefined;
  /** counter: the tinted pill while the number is above zero. */
  you?: boolean | undefined;
  /** counter: a leading glyph (a StatusMark). */
  mark?: ReactNode;
  title?: string | undefined;
  className?: string | undefined;
}

export function Badge({
  tone,
  children,
  count = 0,
  you = false,
  mark,
  title,
  className,
}: BadgeProps) {
  if (tone === "stat") {
    return (
      <span className={s.stat}>
        <TickBadge size={12} />
        <span>{children}</span>
      </span>
    );
  }
  return (
    <span
      className={cx(s.counter, className)}
      data-zero={count === 0 ? "" : undefined}
      data-you={you && count > 0 ? "" : undefined}
      title={title}
    >
      {mark ? (
        <span className={s.mark} aria-hidden="true">
          {mark}
        </span>
      ) : null}
      <b>{count}</b> <span>{children}</span>
    </span>
  );
}
