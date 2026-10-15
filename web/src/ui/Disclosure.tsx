// Disclosure: a chevron toggle button whose open state the caller holds (`aria-expanded`, and
// `aria-controls` naming the region it shows). Fold: the same head on a native `details`, for
// content that only folds (a check's notes, "How each stage was called").
import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./Disclosure.module.css";
import { ChevronIcon } from "./icons";

interface HeadProps {
  /** The label. */
  children: ReactNode;
  /** Quiet text after the label, led by "·" ("2 repairs"). */
  meta?: ReactNode;
  /** meta (default): 12/500 --ink-2. section: 13 --ink-1, for a fold that heads a section. */
  size?: "meta" | "section" | undefined;
}

function HeadParts({ children, meta }: Pick<HeadProps, "children" | "meta">) {
  return (
    <>
      <span className={s.chev}>
        <ChevronIcon />
      </span>
      {children}
      {meta ? <span className={s.meta}>· {meta}</span> : null}
    </>
  );
}

export interface DisclosureProps extends HeadProps {
  expanded: boolean;
  onToggle: () => void;
  /** id of the region this button shows and hides. */
  controls?: string | undefined;
  id?: string | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

export function Disclosure({
  expanded,
  onToggle,
  controls,
  size = "meta",
  meta,
  id,
  title,
  className,
  children,
}: DisclosureProps) {
  return (
    <button
      type="button"
      id={id}
      title={title}
      className={cx(s.head, size === "section" && s.section, className)}
      aria-expanded={expanded}
      aria-controls={controls}
      onClick={onToggle}
    >
      <HeadParts meta={meta}>{children}</HeadParts>
    </button>
  );
}

export interface FoldProps {
  /** The summary's label. */
  summary: ReactNode;
  /** Quiet text after the label, led by "·". */
  meta?: ReactNode;
  size?: HeadProps["size"];
  /** Open on first render. */
  defaultOpen?: boolean | undefined;
  /** Called with the new state after the reader opens or closes it. */
  onToggle?: ((open: boolean) => void) | undefined;
  /** What the fold shows when open. */
  children?: ReactNode;
  className?: string | undefined;
}

export function Fold({
  summary,
  meta,
  size = "meta",
  defaultOpen = false,
  onToggle,
  children,
  className,
}: FoldProps) {
  return (
    <details
      className={cx(s.fold, className)}
      open={defaultOpen}
      onToggle={
        onToggle &&
        ((e) => {
          onToggle(e.currentTarget.open);
        })
      }
    >
      <summary className={cx(s.head, size === "section" && s.section)}>
        <HeadParts meta={meta}>{summary}</HeadParts>
      </summary>
      {children}
    </details>
  );
}
