import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { describeDocumentError, describeSaveError, fixFirst, isForbidden, savedLine } from "./save";

const NOW = Date.parse("2026-10-12T10:00:00Z");

describe("save problems", () => {
  it("describeSaveError maps the 409s", () => {
    expect(describeSaveError(new ApiError(409, "stale_version", "x"))).toEqual({
      text: "Changed by someone else. Reload (your draft will be lost).",
      reload: true,
    });
    expect(describeSaveError(new ApiError(409, "run_active", "x")).text).toMatch(/run is active/);
    expect(
      describeSaveError(new ApiError(422, "invalid_pipeline", "stage lint: needs a command")),
    ).toEqual({ text: "stage lint: needs a command", reload: false });
  });

  it("describeDocumentError offers Reload only on a stale version", () => {
    expect(describeDocumentError(new ApiError(409, "stale_version", "x")).reload).toBe(true);
    expect(describeDocumentError(new ApiError(409, "in_use", "repo r is used"))).toEqual({
      text: "repo r is used",
      reload: false,
    });
  });

  it("says how many issues block Save", () => {
    expect(fixFirst(0)).toBeUndefined();
    expect(fixFirst(1)).toBe("Fix 1 issue first");
    expect(fixFirst(3)).toBe("Fix 3 issues first");
  });
});

describe("savedLine", () => {
  it("names who saved and how long ago", () => {
    const doc = {
      version: 3,
      updated_at: "2026-10-12T09:55:00Z",
      updated_by: { display_name: "Diwash Rai" },
    };
    expect(savedLine(doc, NOW)).toBe("Saved by Diwash · 5m ago");
    expect(savedLine({ ...doc, updated_at: "2026-10-12T10:00:00Z" }, NOW)).toBe(
      "Saved by Diwash · just now",
    );
  });

  it("says a seeded document came from the server config", () => {
    expect(
      savedLine({ version: 1, updated_at: "2026-10-12T09:00:00Z", updated_by: null }, NOW),
    ).toBe("From server config");
  });
});

describe("isForbidden", () => {
  it("is true only for a 403", () => {
    expect(isForbidden(new ApiError(403, "forbidden", "x"))).toBe(true);
    expect(isForbidden(new ApiError(409, "stale_version", "x"))).toBe(false);
    expect(isForbidden(new Error("x"))).toBe(false);
  });
});
