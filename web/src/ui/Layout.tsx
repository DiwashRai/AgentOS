// Stack and Inline: the flex column and row every view lays its parts out on, with the gap as a
// step of the --sp-* scale (1 = 2px … 12 = 36px). Spacer takes the free space in a row.
import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "./cx";
import s from "./Layout.module.css";

/** A step of the --sp-* scale: `4` is `var(--sp-4)` (8px). */
export type Space = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12;

/** The elements a layout box may render as. */
export type LayoutElement =
  "div" | "span" | "section" | "header" | "footer" | "nav" | "ul" | "ol" | "li" | "p";

export type LayoutAlign = "start" | "center" | "end" | "baseline" | "stretch";
export type LayoutJustify = "start" | "center" | "end" | "between";

interface LayoutProps extends HTMLAttributes<HTMLElement> {
  /** Gap between children, as a --sp-* step. None by default. */
  gap?: Space | undefined;
  /** Cross-axis alignment. Stack: stretch; Inline: center. */
  align?: LayoutAlign | undefined;
  /** Main-axis distribution (default start). */
  justify?: LayoutJustify | undefined;
  /** The element (default div). */
  as?: LayoutElement | undefined;
  children?: ReactNode;
}

export type StackProps = LayoutProps;

export interface InlineProps extends LayoutProps {
  /** Let the row wrap onto more lines. */
  wrap?: boolean | undefined;
  /** The gap between wrapped lines, when it differs from `gap`. */
  rowGap?: Space | undefined;
}

const ALIGN: Record<LayoutAlign, string | undefined> = {
  start: s.aStart,
  center: s.aCenter,
  end: s.aEnd,
  baseline: s.aBaseline,
  stretch: s.aStretch,
};

const JUSTIFY: Record<LayoutJustify, string | undefined> = {
  start: s.jStart,
  center: s.jCenter,
  end: s.jEnd,
  between: s.jBetween,
};

function gapClass(prefix: "g" | "r", step: Space | undefined): string | undefined {
  return step === undefined ? undefined : s[`${prefix}${String(step)}`];
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
  const classes = cx(
    s.stack,
    gapClass("g", gap),
    align && ALIGN[align],
    justify && JUSTIFY[justify],
    className,
  );
  return (
    <Tag className={classes} {...rest}>
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
    gapClass("g", gap),
    gapClass("r", rowGap),
    align && ALIGN[align],
    justify && JUSTIFY[justify],
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
