// The TASKS.md plan (`TicketDetail.plan`) in the refresh's form (mock §4.15, HANDOFF §5 "Plan
// list"): a "Plan" head with the derived "k of n done" and a 140×5 bar, then 38px items with a green
// tick badge (done) or a 14px ring (open), no numbers. With 2+ TASKS.md sections each group gets a
// small sentence-case heading with its own count; a headingless plan reads as one list.
import type { Schemas } from "../../api/client";
import { planView, sectionCount } from "../../domain/plan";
import { TickBadge, cx } from "../../ui";
import s from "./PlanList.module.css";

export interface PlanListProps {
  plan: Schemas["PlanItem"][] | null;
  /** The head's element id (the list is labelled by it). */
  id: string;
  /** h2 on the Overview tab, h3 under the header's now band. */
  level?: 2 | 3;
  /** Shown instead of the items when there is no plan. */
  empty: string;
  className?: string | undefined;
}

export function PlanList({ plan, id, level = 2, empty, className }: PlanListProps) {
  const p = planView(plan);
  const H = level === 2 ? "h2" : "h3";
  return (
    <section className={cx(s.plan, className)} aria-labelledby={id}>
      <H id={id} className={s.head}>
        Plan
        {p ? (
          <>
            <span className={s.count}>
              <b>{p.done}</b> of <b>{p.total}</b> done
            </span>
            <span className={s.bar} aria-hidden="true">
              <i style={{ width: `${String(p.pct)}%` }} />
            </span>
          </>
        ) : null}
      </H>
      {p ? null : <p className={s.empty}>{empty}</p>}
      {p?.sections.map((sec, j) => {
        const secId = `${id}-${String(j)}`;
        const labelled = p.headed && sec.section !== null;
        return (
          <div key={j} className={s.sec}>
            {labelled ? (
              <div id={secId} className={s.secHead}>
                <span title={sec.section ?? undefined}>{sec.section}</span>
                <span>{sectionCount(sec)}</span>
              </div>
            ) : null}
            <ol className={s.items} aria-labelledby={labelled ? secId : undefined}>
              {sec.items.map(({ item, index }) => (
                <li key={index} className={cx(s.item, item.done && s.isDone)}>
                  {item.done ? (
                    <TickBadge size={14} label="Done" />
                  ) : (
                    <span className={s.todo} role="img" aria-label="To do" title="To do" />
                  )}
                  <span title={item.raw}>{item.text}</span>
                </li>
              ))}
            </ol>
          </div>
        );
      })}
    </section>
  );
}
