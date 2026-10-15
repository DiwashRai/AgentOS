import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import {
  canCreate,
  clientCommand,
  clientErrorText,
  NAME_MAX,
  nameIssue,
  seenKey,
  setupLines,
  shellWord,
  stateKey,
  TOKEN_ENV,
} from "./model";

const created = { token: "tok_A-b9", server_url: "https://agentos.example.com" };

describe("the command shown once", () => {
  it("starts the client against the server_url it was given", () => {
    expect(clientCommand("sh", created.server_url)).toBe(
      "python worker.py --client --server https://agentos.example.com",
    );
    expect(clientCommand("pwsh", "http://127.0.0.1:8000")).toBe(
      "python worker.py --client --server http://127.0.0.1:8000",
    );
  });

  it("sets the token in the environment first, and never puts it on the command", () => {
    for (const shell of ["pwsh", "sh"] as const) {
      const [set, run] = setupLines(shell, created);
      expect(set).toContain(TOKEN_ENV);
      expect(set).toContain(created.token);
      expect(run).toBe(clientCommand(shell, created.server_url));
      expect(run).not.toContain(created.token);
    }
    expect(setupLines("pwsh", created)[0]).toBe(`$env:${TOKEN_ENV} = 'tok_A-b9'`);
    expect(setupLines("sh", created)[0]).toBe(`export ${TOKEN_ENV}=tok_A-b9`);
  });

  it("quotes a word only when the shell needs it, each shell its own way", () => {
    expect(shellWord("sh", "https://h:8/x?y")).toBe("'https://h:8/x?y'");
    expect(shellWord("sh", "it's")).toBe(`'it'\\''s'`);
    expect(shellWord("pwsh", "it's")).toBe("'it''s'");
    expect(shellWord("pwsh", "http://h/a-b_c.d")).toBe("http://h/a-b_c.d");
  });
});

describe("the name rule", () => {
  it("strips, then needs 1 to NAME_MAX characters", () => {
    expect(nameIssue("")).toBe("empty");
    expect(nameIssue("   ")).toBe("empty");
    expect(nameIssue("work laptop")).toBeNull();
    expect(nameIssue(` ${"x".repeat(NAME_MAX)} `)).toBeNull();
    expect(nameIssue("x".repeat(NAME_MAX + 1))).toBe("long");
  });
});

describe("wording keys", () => {
  const now = Date.parse("2026-10-15T12:00:00Z");

  it("last seen: never, within the minute, or an age", () => {
    expect(seenKey(null, now)).toBe("never");
    expect(seenKey("2026-10-15T11:59:40Z", now)).toBe("now");
    expect(seenKey("2026-10-15T11:50:00Z", now)).toBe("ago");
  });

  it("state: the server's online, else offline, or never connected", () => {
    expect(stateKey({ online: true, last_seen_at: "2026-10-15T11:59:40Z" })).toBe("online");
    expect(stateKey({ online: false, last_seen_at: "2026-10-15T11:50:00Z" })).toBe("offline");
    expect(stateKey({ online: false, last_seen_at: null })).toBe("never");
  });
});

it("offers create to writers and admins only", () => {
  expect(canCreate("writer")).toBe(true);
  expect(canCreate("admin")).toBe(true);
  expect(canCreate("viewer")).toBe(false);
  expect(canCreate("pending")).toBe(false);
  expect(canCreate(undefined)).toBe(false);
});

it("maps the create and revoke failures by code", () => {
  expect(clientErrorText(new ApiError(403, "forbidden", "x"))).toMatch(/writers/);
  expect(clientErrorText(new ApiError(404, "not_found", "x"))).toMatch(/gone/);
  expect(clientErrorText(new ApiError(422, "validation_error", "Name too long"))).toBe(
    "Name too long",
  );
});
