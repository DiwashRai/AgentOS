// The only place components reach the server. Hooks + one hierarchical query-key factory, so
// SSE invalidation (src/api/stream.ts, later) and mutations target keys in one place.
// ESLint forbids importing `api`/`unwrap` outside src/api/.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, unwrap } from "./client";

export const keys = {
  all: ["agentos"] as const,
  me: () => [...keys.all, "me"] as const,
  config: () => [...keys.all, "config"] as const,
  tickets: () => [...keys.all, "tickets"] as const,
  ticket: (key: string) => [...keys.tickets(), key] as const,
};

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

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (username: string) => unwrap(api.POST("/api/auth/login", { body: { username } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useTickets() {
  return useQuery({ queryKey: keys.tickets(), queryFn: () => unwrap(api.GET("/api/tickets")) });
}
