// Badge: Tag plus the small inline labels around the views. `tag` is the kicker Tag itself; the
// other tones each draw one look:
//   count    a count inside a control (a filter pill's number); zero recedes, `you` in you-blue
//   status   a neutral lozenge with a ring (a Jira status), ellipsized
//   counter  a top-bar count: the number, then the caption; zero recedes, `you` is the tinted pill
//   pass     a check's verdict pill, green; fail: the same in crimson
//   ok       a passed word in green; bad: a failed word in crimson
//   stat     a head's statistic led by a mark (the tick by default): "✓ 2 shipped this week"
// count and counter carry `data-zero` (and counter `data-you` while tinted), so a view's container
// queries can hide them without restating the look.
import type { ReactNode } from "react";
import s from "./Badge.module.css";
import { cx } from "./cx";
import { Tag } from "./Tag";
import { TickBadge } from "./TickBadge";

export type BadgeTone =
  "tag" | "count" | "status" | "counter" | "pass" | "fail" | "ok" | "bad" | "stat";

export interface BadgeProps {
  /** The look (default tag). */
  tone?: BadgeTone | undefined;
  /** The text. counter: the caption after the number (in a trailing span); count: unused. */
  children?: ReactNode;
  /** count and counter: the number. Zero draws quiet. */
  count?: number | undefined;
  /** count: the number in you-blue. counter: the tinted pill while the number is above zero. */
  you?: boolean | undefined;
  /** counter: a leading glyph (a StatusMark). stat: the leading mark instead of the tick. */
  mark?: ReactNode;
  /** tag: priority text in the blocked colour. */
  prio?: boolean | undefined;
  /** tag: quiet (--ink-2, 400). */
  quiet?: boolean | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

export function Badge({
  tone = "tag",
  children,
  count = 0,
  you = false,
  mark,
  prio,
  quiet,
  title,
  className,
}: BadgeProps) {
  const zero = count === 0 ? "" : undefined;

  switch (tone) {
    case "tag":
      return (
        <Tag prio={prio} quiet={quiet} title={title} className={className}>
          {children}
        </Tag>
      );

    case "count":
      return (
        <span
          className={cx(s.count, className)}
          data-zero={zero}
          data-you={you ? "" : undefined}
          title={title}
        >
          {count}
        </span>
      );

    case "status":
      return (
        <span className={cx(s.status, className)} title={title}>
          <i aria-hidden="true" />
          <span>{children}</span>
        </span>
      );

    case "counter":
      return (
        <span
          className={cx(s.counter, className)}
          data-zero={zero}
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

    case "pass":
    case "fail":
      return (
        <span className={cx(s.verdict, s[tone], className)} title={title}>
          {children}
        </span>
      );

    case "ok":
    case "bad":
      return (
        <span className={cx(s[tone], className)} title={title}>
          {children}
        </span>
      );

    case "stat":
      return (
        <span className={cx(s.stat, className)} title={title}>
          {mark ?? <TickBadge size={12} />}
          <span>{children}</span>
        </span>
      );
  }
}
