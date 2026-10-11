import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import { keys } from "../api/queries";

/** A 403 `pending_approval`: the session was demoted to pending (or never approved). */
function isPending(error: Error): boolean {
  return error instanceof ApiError && error.status === 403 && error.code === "pending_approval";
}

function recheckMe() {
  void queryClient.invalidateQueries({ queryKey: keys.me() });
}

// A 401 from any read means the session ended: re-check /api/me so the auth guard sends the user
// to /login. (useMe itself maps 401 to null, so it never lands here.) A 403 `pending_approval`
// from any read or write means the user is pending: re-checking /api/me swaps the shell to the
// pending page.
export const queryClient: QueryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
  queryCache: new QueryCache({
    onError: (error) => {
      if ((error instanceof ApiError && error.status === 401) || isPending(error)) recheckMe();
    },
  }),
  mutationCache: new MutationCache({
    onError: (error) => {
      if (isPending(error)) recheckMe();
    },
  }),
});
