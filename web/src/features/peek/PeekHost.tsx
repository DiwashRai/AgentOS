// The Peek host (DESIGN §9): mounted once in the shell, it shows the panel while `?peek=KEY` is
// set. A dialog: focus moves to the panel, Tab is trapped, Esc / the scrim / the Esc button close
// it (removing `peek`, keeping the other params), and focus returns to the opener.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { cx } from "../../ui";
import { trapIndex } from "./model";
import s from "./Peek.module.css";
import { PeekPanel } from "./PeekPanel";
import { usePeek } from "./usePeek";

/** The panel's exit (fade + 24px slide); 0 under reduced motion. */
const EXIT_MS = 260;

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.getClientRects().length > 0,
  );
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function PeekLayer({ ticketKey, onClose }: { ticketKey: string; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  // Enter: next frame, so the transition runs; focus the panel itself (no ring on open).
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      setShown(true);
      panel.current?.focus({ preventScroll: true });
    });
    return () => {
      cancelAnimationFrame(id);
      window.clearTimeout(timer.current);
    };
  }, []);

  const requestClose = useCallback(() => {
    if (timer.current !== undefined) return;
    setShown(false);
    timer.current = window.setTimeout(onClose, reducedMotion() ? 0 : EXIT_MS);
  }, [onClose]);

  // Esc closes from anywhere while open; Tab stays inside the panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        requestClose();
        return;
      }
      const root = panel.current;
      if (e.key !== "Tab" || !root) return;
      const list = focusables(root);
      const active = document.activeElement;
      const index = active instanceof HTMLElement ? list.indexOf(active) : -1;
      const next = trapIndex(index, list.length, e.shiftKey);
      if (next === null) return;
      e.preventDefault();
      (list[next] ?? root).focus();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [requestClose]);

  return (
    <>
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close peek"
        className={cx(s.scrim, shown && s.isOpen)}
        onClick={requestClose}
      />
      <aside
        ref={panel}
        className={cx(s.peek, shown && s.isOpen)}
        role="dialog"
        aria-modal="true"
        aria-label="Ticket peek"
        tabIndex={-1}
      >
        <PeekPanel ticketKey={ticketKey} onClose={requestClose} />
      </aside>
    </>
  );
}

export function PeekHost() {
  const peek = usePeek();
  const open = peek.key !== null;
  const opener = useRef<Element | null>(null);

  // Remember what had focus when the peek opened (the row), and give it back on close. The
  // layer focuses the panel a frame later, so this still sees the opener.
  useLayoutEffect(() => {
    if (!open) return;
    opener.current = document.activeElement;
    return () => {
      const el = opener.current;
      opener.current = null;
      if (el instanceof HTMLElement && el.isConnected) el.focus({ preventScroll: true });
    };
  }, [open]);

  if (peek.key === null) return null;
  return <PeekLayer ticketKey={peek.key} onClose={peek.close} />;
}
