import {
  createContext,
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

type Panel = RefObject<HTMLDivElement | null>;

/** The open dialogs' panels in opening order: the last one is on top. */
const stack: Panel[] = [];

/** The only dialog that acts on Escape and Tab. */
function isTop(panel: Panel): boolean {
  return stack.at(-1) === panel;
}

/** Whether any dialog is open; document shortcuts (`/`, J/K) stay off while one is. */
export function dialogOpen(): boolean {
  return stack.length > 0;
}

/** The user asked the system for reduced motion. */
export function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Where Tab (or Shift+Tab with `back`) goes so focus stays in the panel: the first (last) drawn
 * stop when focus is outside or on the panel itself, the other end when focus is at (or past) the
 * end it moves towards, the panel when it has no stops. Null when the browser's own move stays
 * inside.
 */
function tabTarget(root: HTMLElement, back: boolean): HTMLElement | null {
  // A hidden or collapsed control has no boxes.
  const list = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.getClientRects().length > 0,
  );
  const first = list[0];
  const last = list.at(-1);
  if (!first || !last) return root;

  const active = document.activeElement;
  if (active === root || !active || !root.contains(active)) return back ? last : first;
  const end = back ? first : last;
  const past = back ? Node.DOCUMENT_POSITION_PRECEDING : Node.DOCUMENT_POSITION_FOLLOWING;
  if (active === end || end.compareDocumentPosition(active) & past) return back ? last : first;
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
 * A modal dialog: scrim, panel (`role="dialog"`, `aria-modal`), focus moved in on open (the panel
 * itself, without a ring, unless content already took it) and back to the opener on close if it is
 * still on the page, Tab wrapping at both ends, Escape and the scrim closing it.
 *
 * Keys: the panel handles Escape and Tab in React's bubble phase, after its own controls, so a
 * control that stops Escape (an inline edit, a confirm) keeps the dialog open. A key from outside
 * the panel (focus fell to `body`) is taken on the document in the capture phase. Either way a
 * handled key stops there, so document listeners (a view's Escape) never see it. Dialogs stack:
 * only the last opened acts, and closing it hands keys and focus to the one below.
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
  const titleId = useId();
  const [shown, setShown] = useState(false);
  const closing = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  // Read at the first render, before a child's autofocus moves focus into the panel.
  const [opener] = useState(() => document.activeElement);

  const close = () => {
    if (closing.current) return;
    closing.current = true;
    setShown(false);

    const ms = exitMs > 0 && !reducedMotion() ? exitMs : 0;
    if (ms === 0) onClose();
    else timer.current = window.setTimeout(onClose, ms);
  };

  // Join the stack on top and move focus in. On unmount the top dialog gives focus back to the
  // opener, or to the new top when the opener is gone.
  useLayoutEffect(() => {
    const root = panel.current;
    if (!root) return;
    stack.push(panel);
    if (!root.contains(document.activeElement)) root.focus({ preventScroll: true });

    return () => {
      const wasTop = isTop(panel);
      stack.splice(stack.indexOf(panel), 1);
      if (!wasTop) return;

      if (opener instanceof HTMLElement && opener !== document.body && opener.isConnected) {
        opener.focus({ preventScroll: true });
      } else {
        stack.at(-1)?.current?.focus({ preventScroll: true });
      }
    };
  }, [opener]);

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
    <div className={cx(s.layer, layerClassName)}>
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
