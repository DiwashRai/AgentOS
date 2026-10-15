import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { importErrorText, offersJiraImport, toImportBody } from "./importForm";

describe("import from Jira", () => {
  it("is offered only when the server imports from Jira", () => {
    expect(offersJiraImport(["jira"])).toBe(true);
    expect(offersJiraImport([])).toBe(false);
  });

  it("sends the trimmed ref and never a blank one", () => {
    expect(toImportBody("  SCRUM-1 ")).toEqual({ ref: "SCRUM-1", workflow_id: null });
    expect(toImportBody("https://x.atlassian.net/browse/SCRUM-1", 3)).toEqual({
      ref: "https://x.atlassian.net/browse/SCRUM-1",
      workflow_id: 3,
    });
    expect(toImportBody("")).toBeNull();
    expect(toImportBody("   ")).toBeNull();
  });

  it("words each error code", () => {
    const err = (status: number, code: ApiError["code"], msg = "server says") =>
      importErrorText(new ApiError(status, code, msg));
    expect(err(409, "key_taken")).toMatch(/already uses/);
    expect(err(404, "ticket_not_found")).toBe(
      "Jira can't find that issue, or your account can't see it.",
    );
    expect(err(502, "upstream_error")).toBe("Jira didn't answer as expected: server says");
    expect(err(501, "not_implemented")).toBe("Import isn't set up on this server.");
    expect(err(403, "forbidden")).toMatch(/permission/);
  });

  it("shows the server's message for a bad ref and anything unexpected", () => {
    expect(
      importErrorText(
        new ApiError(422, "validation_error", "ref: not a jira issue key or link to one"),
      ),
    ).toBe("ref: not a jira issue key or link to one");
    expect(importErrorText(new ApiError(502, "http_error", "502 Bad Gateway"))).toBe(
      "502 Bad Gateway",
    );
    expect(importErrorText(new Error("Failed to fetch"))).toBe("Failed to fetch");
    expect(importErrorText("?")).toBe("Something went wrong");
  });
});
