// Sheet: the raised surface with a hairline frame (kit `.ao-sheet`). FramedList: a list reset
// whose rows are split by rules, framed on a sheet or bare in a column.
import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "./cx";
import s from "./Sheet.module.css";

export interface SheetProps extends HTMLAttributes<HTMLElement> {
  /** edge (default): 1px --rule frame, --row-edge, --r-sheet. float: no frame, --surface-shadow,
   *  --r-row (a sheet sitting in a recess, like Talk's). */
  elevation?: "edge" | "float" | undefined;
  /** Inner padding: none (default), sm (12 14) or md (14 16). */
  pad?: "none" | "sm" | "md" | undefined;
  /** The element (default div). */
  as?: "div" | "section" | "article" | "aside" | "header" | undefined;
  children?: ReactNode;
}

export function Sheet({
  elevation = "edge",
  pad = "none",
  as: Tag = "div",
  className,
  children,
  ...rest
}: SheetProps) {
  const classes = cx(
    s.sheet,
    elevation === "float" && s.float,
    pad === "sm" && s.padSm,
    pad === "md" && s.padMd,
    className,
  );
  return (
    <Tag className={classes} {...rest}>
      {children}
    </Tag>
  );
}

export interface FramedListProps extends HTMLAttributes<HTMLElement> {
  /** On a sheet (default), its rows clipped to the corners; false for a bare list in a column. */
  framed?: boolean | undefined;
  /** between (default): a rule between rows. under: a rule under every row, the last included. */
  rules?: "between" | "under" | undefined;
  /** The rows' rule: soft (--rule-soft, default) or firm (--rule). */
  rule?: "soft" | "firm" | undefined;
  /** A firm rule above the first row. */
  topRule?: boolean | undefined;
  /** ul (default) or ol. */
  as?: "ul" | "ol" | undefined;
  /** The rows: `<li>` elements. */
  children?: ReactNode;
}

export function FramedList({
  framed = true,
  rules = "between",
  rule = "soft",
  topRule = false,
  as: Tag = "ul",
  className,
  children,
  ...rest
}: FramedListProps) {
  const classes = cx(
    s.list,
    framed && s.framed,
    rules === "between" ? s.between : s.under,
    rule === "firm" && s.firm,
    topRule && s.topRule,
    className,
  );
  return (
    <Tag className={classes} {...rest}>
      {children}
    </Tag>
  );
}
