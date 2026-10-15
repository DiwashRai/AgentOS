import { useEffect, useLayoutEffect, useRef } from "react";

export interface DocumentKeyOptions {
  /** Listen only while true (default true). */
  enabled?: boolean;
  /** Listen in the capture phase, before any element's own handler (default false: bubble). */
  capture?: boolean;
}

/**
 * One `keydown` listener on the document. The handler is read through a ref, so a new handler on
 * every render never re-adds the listener. A bubble listener never sees a key that an element's
 * handler (or a dialog) stopped; a capture listener sees every key first.
 */
export function useDocumentKey(
  handler: (e: KeyboardEvent) => void,
  { enabled = true, capture = false }: DocumentKeyOptions = {},
): void {
  const latest = useRef(handler);
  useLayoutEffect(() => {
    latest.current = handler;
  });

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      latest.current(e);
    };
    document.addEventListener("keydown", onKey, capture);
    return () => {
      document.removeEventListener("keydown", onKey, capture);
    };
  }, [enabled, capture]);
}
