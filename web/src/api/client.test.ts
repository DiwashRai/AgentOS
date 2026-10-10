import { describe, expect, it } from "vitest";

import { csrfMiddleware } from "./client";

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
