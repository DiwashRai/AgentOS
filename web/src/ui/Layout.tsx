// Stack and Inline: the flex column and row every view lays its parts out on, with the gap as a
// step of the --sp-* scale (1 = 2px … 10 = 28px). Spacer takes the free space in a row.
import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "./cx";
import s from "./Layout.module.css";

/** A step of the --sp-* scale: `4` is `var(--sp-4)` (8px). */
type Space = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10;

const ALIGN = { start: s.aStart, end: s.aEnd, baseline: s.aBaseline };
const JUSTIFY = { end: s.jEnd, between: s.jBetween };

interface LayoutProps extends HTMLAttributes<HTMLElement> {
  /** Gap between children, as a --sp-* step. None by default. */
  gap?: Space | undefined;
  /** Cross-axis alignment. Stack: stretch; Inline: center. */
  align?: keyof typeof ALIGN | undefined;
  /** Main-axis distribution (default start). */
  justify?: keyof typeof JUSTIFY | undefined;
  /** The element (default div). */
  as?: "div" | "span" | "section" | "ul" | "ol" | "li" | undefined;
  children?: ReactNode;
}

export type StackProps = LayoutProps;

interface InlineProps extends LayoutProps {
  /** Let the row wrap onto more lines. */
  wrap?: boolean | undefined;
  /** The gap between wrapped lines, when it differs from `gap`. */
  rowGap?: 3 | undefined;
}

function layoutClasses({ gap, align, justify }: LayoutProps) {
  return [gap && s[`g${String(gap)}`], align && ALIGN[align], justify && JUSTIFY[justify]];
}

/** A flex column (min-width 0, so its children can ellipsize). */
export function Stack({
  gap,
  align,
  justify,
  as: Tag = "div",
  className,
  children,
  ...rest
}: StackProps) {
  return (
    <Tag className={cx(s.stack, ...layoutClasses({ gap, align, justify }), className)} {...rest}>
      {children}
    </Tag>
  );
}

/** A flex row, centred on the cross axis by default (min-width 0, so its children can ellipsize). */
export function Inline({
  gap,
  rowGap,
  wrap = false,
  align,
  justify,
  as: Tag = "div",
  className,
  children,
  ...rest
}: InlineProps) {
  const classes = cx(
    s.inline,
    wrap && s.wrap,
    ...layoutClasses({ gap, align, justify }),
    rowGap && s[`r${String(rowGap)}`],
    className,
  );
  return (
    <Tag className={classes} {...rest}>
      {children}
    </Tag>
  );
}

/** Takes the free space in an Inline, pushing what follows to the far end. */
export function Spacer({ className }: { className?: string | undefined }) {
  return <span className={cx(s.grow, className)} aria-hidden="true" />;
}
