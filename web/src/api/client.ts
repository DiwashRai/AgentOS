import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

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
