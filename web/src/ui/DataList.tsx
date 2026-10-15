// DataList: the parts of the row lists in My queue, Compact and Intake (refresh mock §4.1 `.row`,
// §5.2 `.col-head`, §4.16 `.list-foot`). The view keeps its grid template: it sets `--cols` (a
// grid-template-columns value) on any ancestor, or lays a part out through `className`.
//   DataHead      the column head: labels on `--cols`, each with an optional hide class
//   DataRows      the list of rows (a list reset on a Stack; honours `hidden`)
//   DataRow       one row's surface: raised or flat, the you-wash, open, the 3px status edge and its
//                 hover (lit: lights up, never moves; lift: rises by --lift). Either the row itself
//                 is the click target (spread the peek's item props on it), or it holds a DataLine.
//   DataLine      the row's one-line button on `--cols` (Compact): Enter / Space are the button's
//   DataTitle     the title cell: type and priority icons, then the ellipsized title
//   DataOverflow  "+N more" under a capped list, with a note and an `aria-expanded` toggle
//   DataFoot      the foot bar: key hints, a grow, then the view's summary
// DataRow exposes its state as `data-surface` ("raised" | "flat"), `data-you`, `data-open` and
// `data-edge`, so a view's own cells can follow it (`.row[data-surface="flat"] .upd`).
import {
  type ComponentPropsWithRef,
  type HTMLAttributes,
  type ReactNode,
  type Ref,
  useCallback,
} from "react";
import type { RowEdge } from "../domain/ticketView";
import { litRowClass } from "./Button";
import { cx } from "./cx";
import s from "./DataList.module.css";
import { Kbd } from "./Kbd";
import { Spacer, Stack, type StackProps } from "./Layout";
import { LinkButton } from "./Link";
import { PriorityIcon, type Priority } from "./PriorityIcon";
import { TypeIcon, type TicketType } from "./TypeIcon";

// ------------------------------------------------------------------ head

export interface DataColumn {
  /** The column's label; "" for an unlabelled column (an action). */
  label: string;
  /** The view's class for this column (the one that hides it on a narrow surface). */
  className?: string | undefined;
  /** Right-align the label (an age column). */
  end?: boolean | undefined;
}

export interface DataHeadProps {
  columns: readonly DataColumn[];
  /** The view's class: margins, a scrollbar gutter, a narrow `--cols`. */
  className?: string | undefined;
}

/** The column head over a DataList (hidden from assistive tech: the rows carry the meaning). */
export function DataHead({ columns, className }: DataHeadProps) {
  return (
    <div className={cx(s.head, className)} aria-hidden="true">
      {columns.map((c, i) => (
        <span key={i} className={cx(c.end && s.end, c.className)}>
          {c.label}
        </span>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ rows

export type DataRowsProps = StackProps;

/** The rows: a `ul` (or `as`) with the list reset, one row under the next, gap 2 by default. */
export function DataRows({ gap = 2, as = "ul", className, children, ...rest }: DataRowsProps) {
  return (
    <Stack gap={gap} as={as} className={cx(s.rows, className)} {...rest}>
      {children}
    </Stack>
  );
}

export interface DataRowProps extends HTMLAttributes<HTMLElement> {
  /** The element (default li). */
  as?: "li" | "article" | "div" | undefined;
  ref?: Ref<HTMLElement> | undefined;
  /** raised (default): in flight or needing action. flat: settled, on the recess. */
  surface?: "raised" | "flat" | undefined;
  /** The Needs-you wash over the surface (raised rows only), and a 600 title. */
  you?: boolean | undefined;
  /** Open (selected): the lit surface with a solid ink hairline. */
  open?: boolean | undefined;
  /** The 3px status edge (`rowLook().edge`); none when omitted. */
  edge?: RowEdge | undefined;
  /** Where the edge is drawn: row (default) runs the row's full height; line draws it on the
   *  DataLine, inset 8px top and bottom, so an expansion under the line carries none. */
  edgeOn?: "row" | "line" | undefined;
  /** lit: lights up on hover or keyboard focus inside, never moves; a `litOnRow` button in it fills
   *  blue. lift: rises by --lift on hover or focus (transform only). none (default): static. */
  hover?: "lit" | "lift" | "none" | undefined;
  /** Lay the row's own cells out on `--cols` at the one-line height (Intake). Leave it off when the
   *  row holds a DataLine or the view draws its own grid. */
  grid?: boolean | undefined;
}

/** One row's surface. Spread `peekItemProps(...)` on it to make the row itself the button, or put a
 *  DataLine inside; either way the row draws the ink focus ring. */
export function DataRow({
  as: Tag = "li",
  surface = "raised",
  you = false,
  open = false,
  edge,
  edgeOn = "row",
  hover = "none",
  grid = false,
  ref,
  className,
  children,
  ...rest
}: DataRowProps) {
  // The element varies with `as`, so the row hands each one a callback over the HTMLElement ref.
  const attach = useCallback(
    (el: HTMLElement | null) => {
      if (typeof ref === "function") ref(el);
      else if (ref) ref.current = el;
    },
    [ref],
  );

  const classes = cx(
    s.row,
    edge && (edgeOn === "row" ? s.edgeRow : s.edgeLine),
    hover === "lit" && cx(s.lit, litRowClass),
    hover === "lift" && s.lift,
    grid && s.grid,
    className,
  );
  return (
    <Tag
      ref={ref ? attach : undefined}
      className={classes}
      data-surface={surface}
      data-you={you ? "" : undefined}
      data-open={open ? "" : undefined}
      data-edge={edge}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export type DataLineProps = ComponentPropsWithRef<"button">;

/** The row's one-line button on `--cols`: 42px raised, 38px flat. Its focus ring is drawn on the
 *  row, so it follows an open row's whole surface. */
export function DataLine({ type = "button", className, children, ...rest }: DataLineProps) {
  return (
    <button type={type} className={cx(s.line, className)} {...rest}>
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ title

export interface DataTitleProps {
  /** The title, ellipsized; the full text is its tooltip. */
  text: string;
  /** The type square before it; omit when the type has its own column (Intake). */
  type?: TicketType | undefined;
  /** The priority slot (always reserved, so titles share one x); omit to leave it out. */
  priority?: Priority | null | undefined;
  /** md (default) or lg (a raised My queue row). */
  size?: "md" | "lg" | undefined;
  /** The element (default span). */
  as?: "span" | "div" | undefined;
  className?: string | undefined;
}

/** The title cell: 500 `--ink-1`, 600 on a you row, `--ink-2` on a flat row. */
export function DataTitle({
  text,
  type,
  priority,
  size = "md",
  as: Tag = "span",
  className,
}: DataTitleProps) {
  return (
    <Tag className={cx(s.title, size === "lg" && s.lg, className)}>
      {type ? <TypeIcon type={type} /> : null}
      {priority === undefined ? null : <PriorityIcon priority={priority} />}
      <span title={text}>{text}</span>
    </Tag>
  );
}

// ------------------------------------------------------------------ overflow

export interface DataOverflowProps {
  /** How many rows the cap hides: "+N more". */
  more: number;
  /** The toggle's text ("Show all 50"). */
  toggle: string;
  /** Whether the list shows every row (the toggle's `aria-expanded`). */
  expanded: boolean;
  onToggle: () => void;
  /** The id of the list the toggle expands. */
  controls?: string | undefined;
  /** A quiet note before the toggle, ellipsized ("73 match in Jira, newest 50 listed"). */
  note?: ReactNode;
  /** The note's class (to hide it on a narrow surface). */
  noteClassName?: string | undefined;
  className?: string | undefined;
}

/** The line under a capped list: "+N more", then the note and the toggle at the right. */
export function DataOverflow({
  more,
  toggle,
  expanded,
  onToggle,
  controls,
  note,
  noteClassName,
  className,
}: DataOverflowProps) {
  return (
    <div className={cx(s.overflow, className)}>
      <b>{`+${String(more)} more`}</b>
      <span className={s.overflowEnd}>
        {note ? <span className={cx(s.note, noteClassName)}>{note}</span> : null}
        <LinkButton aria-expanded={expanded} aria-controls={controls} onClick={onToggle}>
          {toggle}
        </LinkButton>
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ foot

export interface KeyHint {
  /** The keycaps, drawn side by side (["J", "K"]). */
  keys: readonly string[];
  /** What they do ("Move"). */
  label: string;
}

export interface DataFootProps {
  /** The key hints at the left. */
  hints?: readonly KeyHint[] | undefined;
  /** The class on every hint (to hide the hints on a narrow surface). */
  hintClassName?: string | undefined;
  /** A `--rule` line above the bar (default true); off for a foot inside a panel (the Peek). */
  rule?: boolean | undefined;
  /** The view's class: padding, gap. */
  className?: string | undefined;
  /** The summary at the right, after the grow. Its `<b>` numbers are 600 `--ink-1`. */
  children?: ReactNode;
}

/** The foot bar: key hints, a grow, then the summary. */
export function DataFoot({
  hints,
  hintClassName,
  rule = true,
  className,
  children,
}: DataFootProps) {
  return (
    <footer className={cx(s.foot, rule && s.ruled, className)}>
      {hints?.map((h) => (
        <span key={h.label} className={cx(s.hint, hintClassName)}>
          {h.keys.length === 1 ? (
            <Kbd>{h.keys[0]}</Kbd>
          ) : (
            <span className={s.caps}>
              {h.keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </span>
          )}
          {h.label}
        </span>
      ))}
      <Spacer />
      {children}
    </footer>
  );
}
