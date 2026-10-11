// The only place components reach the server. Hooks + one hierarchical query-key factory, so
// SSE invalidation (src/api/stream.ts, later) and mutations target keys in one place.
// ESLint forbids importing `api`/`unwrap` outside src/api/.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type Schemas, unwrap } from "./client";

export const keys = {
  all: ["agentos"] as const,
  me: () => [...keys.all, "me"] as const,
  config: () => [...keys.all, "config"] as const,
  tickets: () => [...keys.all, "tickets"] as const,
  ticket: (key: string) => [...keys.tickets(), key] as const,
};

/** Public app config (auth mode, key prefix, defaults). Static for the life of the server. */
export function useConfig() {
  return useQuery({
    queryKey: keys.config(),
    queryFn: () => unwrap(api.GET("/api/config")),
    staleTime: Infinity,
  });
}

/** The signed-in user, or null when signed out (401). */
export function useMe() {
  return useQuery({
    queryKey: keys.me(),
    queryFn: async () => {
      try {
        return await unwrap(api.GET("/api/me"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
  });
}

/** Sign in. Dev auth sends `username`; Jira Cloud `email` + `token`; Jira Data Center `token`. */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["LoginRequest"]) => unwrap(api.POST("/api/auth/login", { body })),
    onSuccess: async (me) => {
      queryClient.setQueryData(keys.me(), me);
      await queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

/** Sign out: forget the user and drop every cached server read. */
export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/auth/logout")),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: keys.tickets() });
      queryClient.setQueryData(keys.me(), null);
    },
  });
}

/** Every open ticket. The backend's SSE stream is still pings-only, so the board polls every 3 s
 *  until src/api/stream.ts maps real events to invalidations. Polling pauses in background tabs. */
export function useTickets() {
  return useQuery({
    queryKey: keys.tickets(),
    queryFn: () => unwrap(api.GET("/api/tickets")),
    refetchInterval: 3000,
  });
}
