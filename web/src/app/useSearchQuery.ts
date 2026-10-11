// The top-bar search lives in the URL (`?q=`), so it survives a reload and sits alongside the
// peek. Typing replaces the history entry (one Back undoes the whole search, not each letter).
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router";

import { parseQuery } from "../domain/search";

export const SEARCH_PARAM = "q";

export function useSearchQuery() {
  const [params, setParams] = useSearchParams();
  const q = params.get(SEARCH_PARAM) ?? "";
  const query = useMemo(() => parseQuery(q), [q]);
  const setQ = useCallback(
    (next: string) => {
      setParams(
        (p) => {
          const out = new URLSearchParams(p);
          if (next === "") out.delete(SEARCH_PARAM);
          else out.set(SEARCH_PARAM, next);
          return out;
        },
        // Synchronous, not a transition: the input is controlled from the URL, so each keystroke
        // must land before the next one or the caret jumps.
        { replace: true, flushSync: true },
      );
    },
    [setParams],
  );
  return { q, query, setQ };
}
