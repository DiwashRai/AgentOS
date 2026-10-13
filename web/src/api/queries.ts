// The only place components reach the server. Hooks + one hierarchical query-key factory, so
// SSE invalidation (src/api/stream.ts, later) and mutations target keys in one place.
// ESLint forbids importing `api`/`unwrap` outside src/api/.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type Schemas, unwrap } from "./client";

export const keys = {
  all: ["agentos"] as const,
  me: () => [...keys.all, "me"] as const,
  config: () => [...keys.all, "config"] as const,
  people: () => [...keys.all, "people"] as const,
  users: () => [...keys.all, "users"] as const,
  template: () => [...keys.all, "template"] as const,
  tickets: () => [...keys.all, "tickets"] as const,
  ticket: (key: string) => [...keys.tickets(), key] as const,
  // Everything below hangs off its ticket, so invalidating a ticket refreshes all of it.
  files: (key: string) => [...keys.ticket(key), "files"] as const,
  file: (key: string, path: string) => [...keys.files(key), path] as const,
  versions: (key: string, path: string) => [...keys.files(key), path, "versions"] as const,
  chat: (key: string) => [...keys.ticket(key), "chat"] as const,
  runs: (key: string) => [...keys.ticket(key), "runs"] as const,
  run: (id: number) => [...keys.all, "run", id] as const,
  runEvents: (id: number) => [...keys.run(id), "events"] as const,
  manifest: (id: number) => [...keys.run(id), "manifest"] as const,
  intake: () => [...keys.all, "intake"] as const,
  settings: (area: SettingsArea) => [...keys.all, "settings", area] as const,
  /** Jira's fields for the Field mappings picker (not a Settings document). */
  jiraFields: () => [...keys.all, "jira-fields"] as const,
};

/** The admin Settings documents (GET/PUT /api/settings/{area}). */
export type SettingsArea = "space" | "repos" | "intake" | "runtimes" | "runner" | "mappings";

/** Public app config (auth mode, Space name, repos, key prefix, runtimes, defaults). Read from the
 *  Settings documents, so it changes rarely: cached until a Settings or Template save here
 *  refetches it (another admin's save shows on the next page load). */
export function useConfig() {
  return useQuery({
    queryKey: keys.config(),
    queryFn: () => unwrap(api.GET("/api/config")),
    staleTime: Infinity,
  });
}

/** The signed-in user, or null when signed out (401). `refetchInterval` (ms) polls it, e.g. while
 *  a pending user waits for approval. */
export function useMe(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: keys.me(),
    refetchInterval: options?.refetchInterval ?? false,
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
    refetchInterval: POLL_MS,
  });
}

const POLL_MS = 3000;
const RUN_POLL_MS = 1000;
/** Keep at most this many log events per run in memory (SPEC §13: last 5,000 lines). */
const MAX_EVENTS = 5000;

type Ticket = Schemas["TicketDetail"];
/** `RunEvent` is a union on `type` (models.py). */
type RunEvent =
  | Schemas["StageStartedEvent"]
  | Schemas["StageFinishedEvent"]
  | Schemas["LogEvent"]
  | Schemas["WarningEvent"];

/** Mutations change server-derived state everywhere (attention, rail, now): refetch the ticket,
 *  its children and the board. Never patch the cache by hand. */
function useInvalidateTicket() {
  const queryClient = useQueryClient();
  return (key: string) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.ticket(key) }),
      queryClient.invalidateQueries({ queryKey: keys.tickets(), exact: true }),
    ]);
}

// ------------------------------------------------------------------ people & template

export function usePeople() {
  return useQuery({
    queryKey: keys.people(),
    queryFn: () => unwrap(api.GET("/api/people")),
    staleTime: 60_000,
  });
}

export function useTemplate() {
  return useQuery({ queryKey: keys.template(), queryFn: () => unwrap(api.GET("/api/template")) });
}

export function useSaveTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["TemplateUpdate"]) => unwrap(api.PUT("/api/template", { body })),
    onSuccess: (t) => {
      queryClient.setQueryData(keys.template(), t);
      // /api/config carries the effective Space defaults (default_runtime, default_model).
      void queryClient.invalidateQueries({ queryKey: keys.config() });
    },
  });
}

// ------------------------------------------------------------------ settings (admin)
// One document per area: GET it whole, PUT it back whole with `base_version` (409 `stale_version`
// if someone saved first; 409 `in_use` when a removal would break something). Admin only, so
// callers gate on `enabled`. A save puts the answer in the cache and refetches what reads it.

export function useSpaceSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings("space"),
    queryFn: () => unwrap(api.GET("/api/settings/space")),
    enabled,
  });
}

/** Saving the Space changes /api/config (name, default repo, key prefix). */
export function useSaveSpaceSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["SpaceSettingsUpdate"]) =>
      unwrap(api.PUT("/api/settings/space", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.settings("space"), data);
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.config() }),
        queryClient.invalidateQueries({ queryKey: keys.intake() }),
      ]);
    },
  });
}

export function useRepoSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings("repos"),
    queryFn: () => unwrap(api.GET("/api/settings/repos")),
    enabled,
  });
}

/** Saving the repos changes /api/config's catalog and every ticket's resolved repo URL/branch. */
export function useSaveRepoSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["RepoSettingsUpdate"]) =>
      unwrap(api.PUT("/api/settings/repos", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.settings("repos"), data);
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.config() }),
        queryClient.invalidateQueries({ queryKey: keys.tickets() }),
      ]);
    },
  });
}

export function useIntakeSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings("intake"),
    queryFn: () => unwrap(api.GET("/api/settings/intake")),
    enabled,
  });
}

/** Saving the queries drops the server's intake cache, so the Intake view refetches. */
export function useSaveIntakeSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["IntakeSettingsUpdate"]) =>
      unwrap(api.PUT("/api/settings/intake", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.settings("intake"), data);
      return queryClient.invalidateQueries({ queryKey: keys.intake() });
    },
  });
}

/** "Test query": runs a JQL live (not cached, nothing saved), at most 5 items. A mutation, so it
 *  runs only when asked; its `error` field carries Jira's complaint (the request still succeeds). */
export function usePreviewIntake() {
  return useMutation({
    mutationFn: (body: Schemas["IntakePreviewRequest"]) =>
      unwrap(api.POST("/api/settings/intake/preview", { body })),
  });
}

export function useRuntimeSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings("runtimes"),
    queryFn: () => unwrap(api.GET("/api/settings/runtimes")),
    enabled,
  });
}

/** Saving the runtimes changes /api/config (runtime names, server default) and what the Template
 *  page validates against. */
export function useSaveRuntimeSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["RuntimeSettingsUpdate"]) =>
      unwrap(api.PUT("/api/settings/runtimes", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.settings("runtimes"), data);
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.config() }),
        queryClient.invalidateQueries({ queryKey: keys.template() }),
      ]);
    },
  });
}

export function useRunnerSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings("runner"),
    queryFn: () => unwrap(api.GET("/api/settings/runner")),
    enabled,
  });
}

/** The runner is read at each dispatch; nothing else in the UI reads it. */
export function useSaveRunnerSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["RunnerSettingsUpdate"]) =>
      unwrap(api.PUT("/api/settings/runner", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.settings("runner"), data);
    },
  });
}

export function useMappingSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings("mappings"),
    queryFn: () => unwrap(api.GET("/api/settings/mappings")),
    enabled,
  });
}

/** Saving re-applies a `base_branch` mapping to the unlocked tickets it set, so every ticket read
 *  (the board and each ticket's base branch) refetches. */
export function useSaveMappingSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["FieldMappingsUpdate"]) =>
      unwrap(api.PUT("/api/settings/mappings", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.settings("mappings"), data);
      return queryClient.invalidateQueries({ queryKey: keys.tickets() });
    },
  });
}

/** Jira's fields for the mapping picker (the server caches them ~10 min). 501 `not_implemented`
 *  without the Jira service account, 502 `upstream_error` when Jira fails: shown at once, never
 *  retried on its own (Retry refetches). Fetched only while the page that asks is open. */
export function useJiraFields(enabled = true) {
  return useQuery({
    queryKey: keys.jiraFields(),
    queryFn: () => unwrap(api.GET("/api/settings/mappings/jira-fields")),
    enabled,
    retry: false,
    staleTime: 10 * 60_000,
  });
}

/** "Try it": runs an unsaved mapping on typed values or on an issue's field. Nothing is saved. */
export function usePreviewMapping() {
  return useMutation({
    mutationFn: (body: Schemas["MappingPreviewRequest"]) =>
      unwrap(api.POST("/api/settings/mappings/preview", { body })),
  });
}

// ------------------------------------------------------------------ users (admin)

/** Every user, disabled ones included. Admin only (403 otherwise), so callers gate on `enabled`. */
export function useUsers(enabled = true) {
  return useQuery({
    queryKey: keys.users(),
    queryFn: () => unwrap(api.GET("/api/users")),
    enabled,
  });
}

/** Change one user's role or disabled state. Disabling also ends their sessions (server-side).
 *  Refetches users and people (owner pickers list only non-disabled users). */
export function usePatchUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Schemas["UserPatch"] & { id: number }) =>
      unwrap(api.PATCH("/api/users/{user_id}", { params: { path: { user_id: id } }, body })),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.users() }),
        queryClient.invalidateQueries({ queryKey: keys.people() }),
      ]),
  });
}

// ------------------------------------------------------------------ tickets

/** One ticket's detail (pipeline, next_stage, active_run_id…). Polled while open, like the board. */
export function useTicket(key: string | null) {
  return useQuery({
    queryKey: keys.ticket(key ?? ""),
    queryFn: () => unwrap(api.GET("/api/tickets/{key}", { params: { path: { key: key ?? "" } } })),
    enabled: key !== null,
    refetchInterval: POLL_MS,
  });
}

export function useCreateTicket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["TicketCreate"]) => unwrap(api.POST("/api/tickets", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.tickets(), exact: true }),
  });
}

export function usePatchTicket(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: (body: Schemas["TicketPatch"]) =>
      unwrap(api.PATCH("/api/tickets/{key}", { params: { path: { key } }, body })),
    onSuccess: () => invalidate(key),
  });
}

/** 409 `stale_version` or `run_active` → the caller shows "changed by someone else, reload". */
export function useSavePipeline(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: (body: Schemas["PipelineUpdate"]) =>
      unwrap(api.PUT("/api/tickets/{key}/pipeline", { params: { path: { key } }, body })),
    onSuccess: () => invalidate(key),
  });
}

// ------------------------------------------------------------------ runs

/** Dispatch / Continue / Retry: `from_stage` omitted = the server's `next_stage`. */
export function useDispatch(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: (fromStage?: string) =>
      unwrap(
        api.POST("/api/tickets/{key}/runs", {
          params: { path: { key } },
          body: { from_stage: fromStage ?? null },
        }),
      ),
    onSuccess: () => invalidate(key),
  });
}

export function useCancelRun(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: (runId: number) =>
      unwrap(api.POST("/api/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSuccess: () => invalidate(key),
  });
}

export function useRuns(key: string) {
  return useQuery({
    queryKey: keys.runs(key),
    queryFn: () => unwrap(api.GET("/api/tickets/{key}/runs", { params: { path: { key } } })),
    refetchInterval: POLL_MS,
  });
}

/** One run with its stage_runs; polled every second while `active`. */
export function useRun(id: number | null, active: boolean) {
  return useQuery({
    queryKey: keys.run(id ?? 0),
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: id ?? 0 } } })),
    enabled: id !== null,
    refetchInterval: active ? RUN_POLL_MS : false,
  });
}

/**
 * A run's events, fetched incrementally: each poll asks only for events after the last seq it has
 * and appends them (keeping the newest MAX_EVENTS). Polls every second while `active`.
 */
export function useRunEvents(id: number | null, active: boolean) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: keys.runEvents(id ?? 0),
    queryFn: async (): Promise<RunEvent[]> => {
      const runId = id ?? 0;
      const have = queryClient.getQueryData<RunEvent[]>(keys.runEvents(runId)) ?? [];
      const after = have.at(-1)?.seq;
      const fresh: RunEvent[] = [];
      // Drain in pages of 1000 so a reopened finished run loads fully.
      for (;;) {
        const lastSeq = fresh.at(-1)?.seq ?? after;
        const page = await unwrap(
          api.GET("/api/runs/{run_id}/events", {
            params: {
              path: { run_id: runId },
              query: { limit: 1000, ...(lastSeq === undefined ? {} : { after_seq: lastSeq }) },
            },
          }),
        );
        fresh.push(...page);
        if (page.length < 1000) break;
      }
      return fresh.length === 0 ? have : [...have, ...fresh].slice(-MAX_EVENTS);
    },
    enabled: id !== null,
    refetchInterval: active ? RUN_POLL_MS : false,
  });
}

/** Exactly what was sent to the worker; can be MiBs, so fetched only when the view opens. */
export function useManifest(id: number | null) {
  return useQuery({
    queryKey: keys.manifest(id ?? 0),
    queryFn: () =>
      unwrap(api.GET("/api/runs/{run_id}/manifest", { params: { path: { run_id: id ?? 0 } } })),
    enabled: id !== null,
    staleTime: Infinity,
  });
}

// ------------------------------------------------------------------ files (the bundle)

export function useFiles(key: string) {
  return useQuery({
    queryKey: keys.files(key),
    queryFn: () => unwrap(api.GET("/api/tickets/{key}/files", { params: { path: { key } } })),
    refetchInterval: POLL_MS,
  });
}

export function useFile(key: string, path: string | null) {
  return useQuery({
    queryKey: keys.file(key, path ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/tickets/{key}/files/{path}", {
          params: { path: { key, path: path ?? "" } },
        }),
      ),
    enabled: path !== null,
  });
}

export function useFileVersions(key: string, path: string | null) {
  return useQuery({
    queryKey: keys.versions(key, path ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/tickets/{key}/versions/{path}", {
          params: { path: { key, path: path ?? "" } },
        }),
      ),
    enabled: path !== null,
  });
}

/** Create (`base_version: 0`) or update a file. 409 `stale_version` / `bundle_locked` → caller. */
export function useSaveFile(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: ({ path, ...body }: Schemas["FileWrite"] & { path: string }) =>
      unwrap(api.PUT("/api/tickets/{key}/files/{path}", { params: { path: { key, path } }, body })),
    onSuccess: () => invalidate(key),
  });
}

export function useDeleteFile(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: ({ path, baseVersion }: { path: string; baseVersion: number }) =>
      unwrap(
        api.DELETE("/api/tickets/{key}/files/{path}", {
          params: { path: { key, path }, query: { base_version: baseVersion } },
        }),
      ),
    onSuccess: () => invalidate(key),
  });
}

// ------------------------------------------------------------------ chat (Talk)

/** The latest messages, oldest first. Polled until SSE ships. */
export function useChat(key: string | null) {
  return useQuery({
    queryKey: keys.chat(key ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/tickets/{key}/chat", { params: { path: { key: key ?? "" } } })),
    enabled: key !== null,
    refetchInterval: POLL_MS,
  });
}

export function usePostChat(key: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: string) =>
      unwrap(api.POST("/api/tickets/{key}/chat", { params: { path: { key } }, body: { body } })),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.chat(key) }),
        queryClient.invalidateQueries({ queryKey: keys.tickets(), exact: true }),
      ]),
  });
}

export type { Ticket, RunEvent };

/** Take in a source issue by key or link (`ImportResult`; `created: false` = imported before). */
export function useImportTicket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ImportRequest"]) =>
      unwrap(api.POST("/api/tickets/import", { body })),
    onSuccess: (r) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.tickets(), exact: true }),
        queryClient.invalidateQueries({ queryKey: keys.ticket(r.ticket.key) }),
        // Stale but not refetched now: the Intake view shows "Taken in" for a moment and then
        // refetches itself; anywhere else, the next visit to Intake refetches.
        queryClient.invalidateQueries({ queryKey: keys.intake(), refetchType: "none" }),
      ]),
  });
}

/** The Space's intake queries with their Jira candidates (issues not taken in yet). The server
 *  caches each query for ~45 s, so polling faster than a minute gains nothing. */
export function useIntake() {
  return useQuery({
    queryKey: keys.intake(),
    queryFn: () => unwrap(api.GET("/api/intake")),
    refetchInterval: 60_000,
  });
}

/** Refresh / Retry: `?fresh=1` asks Jira again instead of the ~45 s cache (a query fetched in the
 *  last few seconds is still served from it), and the answer replaces the Intake list. */
export function useRefreshIntake() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.GET("/api/intake", { params: { query: { fresh: true } } })),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.intake(), data);
    },
  });
}

/** Close a ticket as completed (state done) or abandoned (state closed), with an optional note.
 *  409 `run_active` while a run is active. Reopen = PATCH `state: open` (clears the resolution). */
export function useResolveTicket(key: string) {
  const invalidate = useInvalidateTicket();
  return useMutation({
    mutationFn: (body: Schemas["ResolveRequest"]) =>
      unwrap(api.POST("/api/tickets/{key}/resolve", { params: { path: { key } }, body })),
    onSuccess: () => invalidate(key),
  });
}
