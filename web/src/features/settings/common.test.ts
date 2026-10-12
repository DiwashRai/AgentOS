import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { isForbidden, savedLine } from "./common";

const NOW = Date.parse("2026-10-12T10:00:00Z");

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
