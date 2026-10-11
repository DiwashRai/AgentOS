import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { jiraHost, loginErrorText } from "./loginError";

describe("loginErrorText", () => {
  it.each([
    ["invalid_credentials", 401, /didn't accept/],
    ["no_project_access", 403, /can't see any of this Space's projects/],
    ["user_disabled", 403, /disabled/],
    ["rate_limited", 429, /Too many/],
    ["missing_credentials", 400, /every field/],
    ["upstream_error", 502, /Jira didn't answer/],
  ] as const)("%s → friendly text", (code, status, expected) => {
    expect(loginErrorText(new ApiError(status, code, "server says"))).toMatch(expected);
  });

  it("falls back to the server message for other codes", () => {
    expect(loginErrorText(new ApiError(500, "internal_error", "Boom"))).toBe("Boom");
  });

  it("uses the message of a non-API error", () => {
    expect(loginErrorText(new Error("Failed to fetch"))).toBe("Failed to fetch");
  });
});

describe("jiraHost", () => {
  it.each([
    ["https://acme.atlassian.net", "acme.atlassian.net"],
    ["https://jira.example.com:8443/jira", "jira.example.com:8443"],
    ["not a url", "not a url"],
  ])("%s → %s", (url, expected) => {
    expect(jiraHost(url)).toBe(expected);
  });
});
