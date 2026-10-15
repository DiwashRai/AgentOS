// Checklist: a plan as `planView` (domain/plan.ts) gives it. Each item is a green tick badge (done)
// or a 14px ring (to do), then its plain text with the raw text as the title; "+N more" closes a
// capped list. Two densities:
//   roomy    the Ticket page: a section heading with "k of n done" and the bar; 38px items under
//            soft rules that wrap; with 2+ TASKS.md sections, each gets a small head with its count
//   compact  the Peek and Compact: a 12px label with "k of n done" at its end (the Peek) or right
//            after it, "Plan · k of n done" (Compact); 19px one-line items
// Without a plan it draws the head and then `empty`.
import type { ReactNode } from "react";
import type { PlanItemView, PlanView } from "../domain/plan";
import { sectionCount } from "../domain/plan";
import s from "./Checklist.module.css";
import { cx } from "./cx";
import text from "./text.module.css";
import { TickBadge } from "./TickBadge";

export interface ChecklistProps {
  /** The plan from `planView(plan, max?)`; null for no plan. */
  plan: PlanView | null;
  /** roomy (default) or compact. */
  density?: "roomy" | "compact" | undefined;
  /** The head's text ("Plan"). Without one there is no head, and `label` names the list. */
  title?: ReactNode;
  /** The head's element: h2 (default), h3 or h4. */
  level?: 2 | 3 | 4 | undefined;
  /** The head's id; the list (or each unheaded list) is labelled by it. Without one, a list under a
   *  string title is named by the title. */
  id?: string | undefined;
  /** The list's accessible name when there is no head. */
  label?: string | undefined;
  /** bar: "k of n done" and the bar (roomy default). count: "k of n done" at the head's end
   *  (compact default). inline: "· k of n done" right after the title (compact). none: the title
   *  alone. */
  progress?: "bar" | "count" | "inline" | "none" | undefined;
  /** Draw the section heads when the plan has 2+ sections (default: roomy only). */
  sections?: boolean | undefined;
  /** Drawn under the head when there is no plan (an EmptyState note). */
  empty?: ReactNode;
  className?: string | undefined;
}

function Item({ item }: { item: PlanItemView }) {
  return (
    <li className={cx(s.item, item.done && s.isDone)}>
      {item.done ? (
        <TickBadge size={14} label="Done" />
      ) : (
        <span className={s.todo} role="img" aria-label="To do" title="To do" />
      )}
      <span title={item.raw}>{item.text}</span>
    </li>
  );
}

function More({ n }: { n: number }) {
  return n > 0 ? <li className={s.more}>{`+${String(n)} more`}</li> : null;
}

export function Checklist({
  plan,
  density = "roomy",
  title,
  level = 2,
  id,
  label,
  progress = density === "roomy" ? "bar" : "count",
  sections = density === "roomy",
  empty,
  className,
}: ChecklistProps) {
  const H = `h${String(level)}` as "h2" | "h3" | "h4";
  const roomy = density === "roomy";
  let listName: { "aria-labelledby"?: string; "aria-label"?: string | undefined };
  if (title !== undefined && id) listName = { "aria-labelledby": id };
  else if (typeof title === "string") listName = { "aria-label": title };
  else listName = { "aria-label": label };

  const head =
    title !== undefined ? (
      <H id={id} className={roomy ? cx(text.heading, s.head) : s.lab}>
        {roomy ? title : <span>{title}</span>}
        {plan && progress !== "none" && roomy ? (
          <span className={s.count}>
            <b>{plan.done}</b> of <b>{plan.total}</b> done
          </span>
        ) : null}
        {plan && progress !== "none" && !roomy ? (
          <span className={progress === "inline" ? s.labInline : s.labCount}>
            {`${progress === "inline" ? "· " : ""}${String(plan.done)} of ${String(plan.total)} done`}
          </span>
        ) : null}
        {plan && progress === "bar" ? (
          <span className={s.bar} aria-hidden="true">
            <i style={{ width: `${String(plan.pct)}%` }} />
          </span>
        ) : null}
      </H>
    ) : null;

  let body: ReactNode;
  if (!plan) {
    body = empty;
  } else if (sections && plan.headed) {
    body = plan.sections.map((sec, j) => {
      const secId = id ? `${id}-${String(j)}` : undefined;
      const headed = sec.section !== null;
      const last = j === plan.sections.length - 1;
      return (
        <div key={j} className={s.sec}>
          {headed ? (
            <div id={secId} className={s.secHead}>
              <span title={sec.section ?? undefined}>{sec.section}</span>
              <span>{sectionCount(sec)}</span>
            </div>
          ) : null}
          <ol className={s.items} {...(headed && secId ? { "aria-labelledby": secId } : listName)}>
            {sec.items.map(({ item, index }) => (
              <Item key={index} item={item} />
            ))}
            {last ? <More n={plan.more} /> : null}
          </ol>
        </div>
      );
    });
  } else {
    body = (
      <ol className={s.items} {...listName}>
        {plan.items.map((item, i) => (
          <Item key={i} item={item} />
        ))}
        <More n={plan.more} />
      </ol>
    );
  }

  return (
    <div className={cx(s.root, roomy ? s.roomy : s.compact, className)}>
      {head}
      {body}
    </div>
  );
}
