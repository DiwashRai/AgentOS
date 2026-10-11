import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { cx } from "./cx";
import { tabTarget } from "./tabKeys";
import s from "./Tabs.module.css";

export interface TabItem<Id extends string = string> {
  id: Id;
  label: string;
  count?: number | undefined;
}

export interface TabsProps<Id extends string = string> {
  items: TabItem<Id>[];
  value: Id;
  onChange: (id: Id) => void;
  /** Accessible name of the tablist. */
  label: string;
  /** Right slot, outside the tablist (mono hints, links). */
  right?: ReactNode;
  /** When set, tabs get `id="{idPrefix}-tab-{id}"` and `aria-controls="{idPrefix}-panel-{id}"`. */
  idPrefix?: string | undefined;
  className?: string | undefined;
}

export function Tabs<Id extends string = string>({
  items,
  value,
  onChange,
  label,
  right,
  idPrefix,
  className,
}: TabsProps<Id>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  // Keep one tab stop even if `value` matches no tab.
  const focusable = items.some((it) => it.id === value) ? value : items[0]?.id;

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const target = tabTarget(index, e.key, items.length);
    const item = target === null ? undefined : items[target];
    if (target === null || !item) return;
    e.preventDefault();
    onChange(item.id);
    refs.current[target]?.focus();
  }

  return (
    <div className={cx(s.tabs, className)}>
      <div className={s.list} role="tablist" aria-label={label}>
        {items.map((it, i) => {
          const selected = it.id === value;
          return (
            <button
              key={it.id}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={idPrefix ? `${idPrefix}-tab-${it.id}` : undefined}
              aria-controls={idPrefix ? `${idPrefix}-panel-${it.id}` : undefined}
              aria-selected={selected}
              tabIndex={it.id === focusable ? 0 : -1}
              onClick={() => {
                onChange(it.id);
              }}
              onKeyDown={(e) => {
                onKeyDown(e, i);
              }}
            >
              {it.label}
              {it.count !== undefined ? <span>{it.count}</span> : null}
            </button>
          );
        })}
      </div>
      {right ? <div className={s.right}>{right}</div> : null}
    </div>
  );
}
