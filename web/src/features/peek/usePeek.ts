// The peek lives in the URL (`?peek=KEY`) so it works over every view and survives a reload.
import type { KeyboardEvent, MouseEvent } from "react";
import { useSearchParams } from "react-router";

import { withoutParam, withParam } from "./params";

export const PEEK_PARAM = "peek";

/** True when `target` sits in a button, link, field or form (an inline confirm) inside `item`
 *  (not the item itself). */
export function isInnerControl(target: EventTarget | null, item: Element): boolean {
  if (!(target instanceof Element)) return false;
  const control = target.closest("button, a, input, textarea, select, form");
  return control !== null && control !== item && item.contains(control);
}

export function usePeek() {
  const [params, setParams] = useSearchParams();
  return {
    key: params.get(PEEK_PARAM),
    open: (key: string) => {
      setParams((p) => withParam(p, PEEK_PARAM, key));
    },
    close: () => {
      setParams((p) => withoutParam(p, PEEK_PARAM));
    },
  };
}

/**
 * Props that make a whole row / tile / rack line open the peek on click, Enter or Space
 * (DESIGN §15). Buttons and links inside keep their own action: clicks on them are ignored here,
 * and keys only count when the item itself has focus.
 */
export function peekItemProps(title: string, open: () => void) {
  return {
    role: "button" as const,
    tabIndex: 0,
    "aria-label": title,
    onClick: (e: MouseEvent<HTMLElement>) => {
      if (isInnerControl(e.target, e.currentTarget)) return;
      open();
    },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.target !== e.currentTarget) return;
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      open();
    },
  };
}
