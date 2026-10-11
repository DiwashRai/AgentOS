import { QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import { keys } from "../api/queries";

// A 401 from any read means the session ended: re-check /api/me so the auth guard sends the user
// to /login. (useMe itself maps 401 to null, so it never lands here.)
export const queryClient: QueryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
  queryCache: new QueryCache({
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        void queryClient.invalidateQueries({ queryKey: keys.me() });
      }
    },
  }),
});
