import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";

import { cx } from "./cx";
import s from "./Dialog.module.css";
import { useDocumentKey } from "./useDocumentKey";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
  '[contenteditable="true"]',
  "[tabindex]",
]
  .map((sel) => `${sel}:not([tabindex="-1"])`)
  .join(", ");

/** How long after opening content that loads late may still bring the autofocus field. */
const LATE_AUTOFOCUS_MS = 2000;

type Panel = RefObject<HTMLDivElement | null>;

/** An open dialog: its panel and the fixed layer whose z-index sets where it is painted. */
interface Entry {
  panel: Panel;
  layer: RefObject<HTMLDivElement | null>;
}

/** The open dialogs, in mount order. */
const stack: Entry[] = [];

function zIndexOf(entry: Entry): number {
  const layer = entry.layer.current;
  if (!layer) return 0;
  const z = Number.parseInt(getComputedStyle(layer).zIndex, 10);
  return Number.isNaN(z) ? 0 : z;
}

/** The dialog painted on top: the highest layer z-index, a later mount winning a tie. */
function topEntry(): Entry | undefined {
  let top: Entry | undefined;
  let topZ = -Infinity;
  for (const entry of stack) {
    const z = zIndexOf(entry);
    if (z >= topZ) {
      top = entry;
      topZ = z;
    }
  }
  return top;
}

/** The only dialog that acts on Escape and Tab. */
function isTop(panel: Panel): boolean {
  return topEntry()?.panel === panel;
}

/** Whether any dialog is open; document shortcuts (`/`, J/K) stay off while one is. */
export function dialogOpen(): boolean {
  return stack.length > 0;
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The panel's tab stops that are drawn (a hidden or collapsed control has no boxes). */
function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.getClientRects().length > 0,
  );
}

/**
 * Where Tab (or Shift+Tab with `back`) goes so focus stays in the panel: the other end when it
 * would leave, the first (last) stop when focus is outside or on the panel itself, the panel when
 * it has no stops. Focus inside on something that is not a stop moves to the next (previous) stop
 * in document order, wrapping at the ends. Null when the browser's own move stays inside.
 */
function tabTarget(root: HTMLElement, back: boolean): HTMLElement | null {
  const list = focusables(root);
  const first = list[0];
  const last = list.at(-1);
  if (!first || !last) return root;

  const active = document.activeElement;
  const inside = active !== root && active !== null && root.contains(active);
  if (!inside) return back ? last : first;

  if (!list.some((el) => el === active)) {
    if (back) {
      const before = list.filter(
        (el) => active.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING,
      );
      return before.at(-1) ?? last;
    }
    const after = list.find(
      (el) => active.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING,
    );
    return after ?? first;
  }

  if (back && active === first) return last;
  if (!back && active === last) return first;
  return null;
}

/** The parts of a native or React key event the dialog uses. */
interface KeyEventLike {
  key: string;
  shiftKey: boolean;
  preventDefault: () => void;
  stopPropagation: () => void;
}

/** Escape closes and Tab wraps. A handled key stops there, so no outer listener sees it. */
function handleKey(e: KeyEventLike, root: HTMLElement, close: () => void): void {
  if (e.key === "Escape") {
    close();
  } else if (e.key === "Tab") {
    const next = tabTarget(root, e.shiftKey);
    if (next === null) return;
    next.focus({ preventScroll: true });
  } else {
    return;
  }

  e.preventDefault();
  e.stopPropagation();
}

const TitleId = createContext<string | undefined>(undefined);

export interface DialogProps {
  /**
   * Called once the dialog has closed (after `exitMs`): by Escape, the scrim, or the `close`
   * passed to `children`. The host unmounts the dialog in it (usually by dropping a URL param).
   */
  onClose: () => void;
  /** The dialog's accessible name. Without it the dialog is labelled by its `DialogTitle`. */
  label?: string;
  /** Panel geometry and motion, styled on `[data-open]`. Replaces the default centred modal. */
  className?: string;
  /** Extra class on the fixed layer; set `--dialog-z` there to change its z-index (default 50). */
  layerClassName?: string;
  /** Names the scrim as a button (e.g. "Close peek"); without it the scrim is hidden from AT. */
  scrimLabel?: string;
  /** How long it stays rendered after closing, to animate out (ms; 0 under reduced motion). */
  exitMs?: number;
  /** The panel's content, or a function given `close` (which runs the exit, then `onClose`). */
  children: ReactNode | ((close: () => void) => ReactNode);
}

/**
 * A modal dialog: scrim, panel (`role="dialog"`, `aria-modal`), focus moved in on open (the
 * first `[data-autofocus]` element, else the panel itself without a ring) and back to the opener
 * on close if it is still on the page, Tab wrapping at both ends, Escape and the scrim closing it.
 *
 * Keys: the panel handles Escape and Tab in React's bubble phase, after its own controls, so a
 * control that stops Escape (an inline edit, a confirm) keeps the dialog open. A key from outside
 * the panel (focus fell to `body`) is taken on the document in the capture phase. Either way a
 * handled key stops there, so document listeners (a view's Escape) never see it. Dialogs stack:
 * only the one painted on top acts (highest layer z-index, then the most recently opened), and
 * closing it hands keys and focus to the next one down.
 */
export function Dialog({
  onClose,
  label,
  className,
  layerClassName,
  scrimLabel,
  exitMs = 0,
  children,
}: DialogProps) {
  const panel = useRef<HTMLDivElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [shown, setShown] = useState(false);
  const closing = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  const latestClose = useRef(onClose);
  useLayoutEffect(() => {
    latestClose.current = onClose;
  });

  const close = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    setShown(false);

    const ms = exitMs > 0 && !reducedMotion() ? exitMs : 0;
    if (ms === 0) {
      latestClose.current();
      return;
    }
    timer.current = window.setTimeout(() => {
      latestClose.current();
    }, ms);
  }, [exitMs]);

  // Join the stack, remember the opener and, when this dialog is painted on top, move focus in. On
  // unmount the top dialog gives focus back to the opener, or to the new top when the opener is gone.
  useLayoutEffect(() => {
    const root = panel.current;
    if (!root) return;
    const opener = document.activeElement;
    const entry: Entry = { panel, layer };
    stack.push(entry);
    const onTop = isTop(panel);

    const target = root.querySelector<HTMLElement>("[data-autofocus]");
    if (onTop) (target ?? root).focus({ preventScroll: true });

    // Content that loads soon after opening may bring the autofocus field: focus it, unless focus
    // has already moved off the panel. The watch ends on the first match or after a short window.
    const late =
      target || !onTop
        ? null
        : new MutationObserver((_, observer) => {
            if (document.activeElement !== root) {
              observer.disconnect();
              return;
            }
            const found = root.querySelector<HTMLElement>("[data-autofocus]");
            if (!found) return;
            observer.disconnect();
            found.focus({ preventScroll: true });
          });
    late?.observe(root, { childList: true, subtree: true });
    const lateEnd = late
      ? window.setTimeout(() => {
          late.disconnect();
        }, LATE_AUTOFOCUS_MS)
      : undefined;

    return () => {
      late?.disconnect();
      window.clearTimeout(lateEnd);
      const wasTop = isTop(panel);
      stack.splice(stack.indexOf(entry), 1);
      if (!wasTop) return;

      if (opener instanceof HTMLElement && opener !== document.body && opener.isConnected) {
        opener.focus({ preventScroll: true });
      } else {
        topEntry()?.panel.current?.focus({ preventScroll: true });
      }
    };
  }, []);

  // Enter on the next frame, so the open transition runs.
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      if (!closing.current) setShown(true);
    });
    return () => {
      cancelAnimationFrame(id);
      window.clearTimeout(timer.current);
    };
  }, []);

  // A key whose target is outside the top panel (focus on `body`, or left on a dialog below).
  useDocumentKey(
    (e) => {
      const root = panel.current;
      if (!root || !isTop(panel) || e.isComposing) return;
      if (e.target instanceof Node && root.contains(e.target)) return;
      handleKey(e, root, close);
    },
    { capture: true },
  );

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const root = panel.current;
    if (!root || !isTop(panel) || e.nativeEvent.isComposing) return;
    handleKey(e, root, close);
  };

  const open = shown ? "" : undefined;
  return (
    <div ref={layer} className={cx(s.layer, layerClassName)}>
      <button
        type="button"
        tabIndex={-1}
        className={s.scrim}
        aria-label={scrimLabel}
        aria-hidden={scrimLabel ? undefined : true}
        data-open={open}
        onClick={close}
      />
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- the modal's Escape and Tab trap, bubbling from its controls */}
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-labelledby={label ? undefined : titleId}
        tabIndex={-1}
        className={cx(s.panel, className ?? s.centred)}
        data-open={open}
        onKeyDown={onKeyDown}
      >
        <TitleId value={titleId}>
          {typeof children === "function" ? children(close) : children}
        </TitleId>
      </div>
    </div>
  );
}

export interface DialogTitleProps {
  children: ReactNode;
  /** Margins and placement; the type comes from the dialog. */
  className?: string;
}

/** The dialog's heading (`h2`), which names a dialog that has no `label`. */
export function DialogTitle({ children, className }: DialogTitleProps) {
  const id = useContext(TitleId);
  return (
    <h2 id={id} className={cx(s.title, className)}>
      {children}
    </h2>
  );
}
