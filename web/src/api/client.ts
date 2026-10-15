import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type ErrorCode = Schemas["ErrorDetail"]["code"];
export type ValidationIssue = Schemas["ValidationIssue"];

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Every non-GET /api request must carry `X-AgentOS: 1` (SPEC §10, CSRF). */
export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method)) request.headers.set("X-AgentOS", "1");
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
api.use(csrfMiddleware);

/**
 * A failed API call. `code` comes from the error envelope, or `http_error` if there was none.
 * `issues` lists every problem of a 422, each at its field `loc` (relative to the request body;
 * empty = the request as a whole); it is empty on other errors and without an envelope.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode,
    message: string,
    readonly issues: readonly ValidationIssue[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isEnvelope(value: unknown): value is Schemas["ErrorBody"] {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const detail: unknown = value.error;
  return (
    typeof detail === "object" &&
    detail !== null &&
    "code" in detail &&
    "message" in detail &&
    "issues" in detail &&
    Array.isArray(detail.issues)
  );
}

/**
 * Resolve an openapi-fetch call: return `data` on 2xx, throw `ApiError` otherwise.
 * Always branch on `response.ok`. A proxy 502 or a reloading backend has an empty body, which
 * openapi-fetch reports as a falsy `error`, so `if (error)` alone would treat it as success.
 */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok) return data as T;
  if (isEnvelope(error)) {
    const { code, message, issues } = error.error;
    throw new ApiError(response.status, code, message, issues);
  }
  throw new ApiError(
    response.status,
    "http_error",
    `${String(response.status)} ${response.statusText}`.trim(),
  );
}
