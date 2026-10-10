import { describe, expect, it } from "vitest";

import { ApiError, csrfMiddleware, unwrap } from "./client";

async function headerFor(method: string): Promise<string | null> {
  const request = new Request("http://localhost/api/tickets", { method });
  const result = await csrfMiddleware.onRequest?.({
    request,
    schemaPath: "/api/tickets",
    params: {},
    id: "1",
    options: {} as never,
  });
  return result instanceof Request ? result.headers.get("X-AgentOS") : null;
}

describe("csrfMiddleware", () => {
  it("adds the header to mutations", async () => {
    expect(await headerFor("POST")).toBe("1");
  });

  it("leaves safe methods alone", async () => {
    expect(await headerFor("GET")).toBeNull();
  });
});

describe("unwrap", () => {
  it("returns data on success", async () => {
    const data = await unwrap(Promise.resolve({ data: 42, response: new Response(null) }));
    expect(data).toBe(42);
  });

  it("throws the envelope's code", async () => {
    const error = { error: { code: "stale_version", message: "changed" } };
    const call = unwrap(Promise.resolve({ error, response: new Response(null, { status: 409 }) }));
    await expect(call).rejects.toMatchObject({ status: 409, code: "stale_version" });
  });

  it("throws on an empty-body proxy error instead of treating it as success", async () => {
    const response = new Response(null, { status: 502, statusText: "Bad Gateway" });
    const call = unwrap(Promise.resolve({ error: "", response }));
    await expect(call).rejects.toBeInstanceOf(ApiError);
    await expect(call).rejects.toMatchObject({ status: 502, code: "http_error" });
  });
});
