import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { LOCKED_TEXT, STALE_TEXT, writeProblem } from "./errors";

describe("writeProblem", () => {
  it("stale_version on save/delete → keep the text, offer reload", () => {
    const err = new ApiError(409, "stale_version", "x was changed by someone else");
    expect(writeProblem(err, "save")).toEqual({ text: STALE_TEXT, reload: true });
    expect(writeProblem(err, "delete")).toEqual({ text: STALE_TEXT, reload: true });
  });

  it("stale_version on create → the path is taken", () => {
    const err = new ApiError(409, "stale_version", "x already exists");
    expect(writeProblem(err, "create")).toEqual({
      text: "A file with this path already exists.",
      reload: false,
    });
  });

  it("bundle_locked → a run is working on these files", () => {
    const err = new ApiError(409, "bundle_locked", "Context is read-only while run #3 is running");
    expect(writeProblem(err, "save")).toEqual({ text: LOCKED_TEXT, reload: false });
  });

  it("422 shows the server's message", () => {
    const err = new ApiError(422, "validation_error", "path: segment '..' is not allowed");
    expect(writeProblem(err, "create")).toEqual({
      text: "path: segment '..' is not allowed",
      reload: false,
    });
  });

  it("non-API errors fall back to their message", () => {
    expect(writeProblem(new Error("Failed to fetch"), "save").text).toBe("Failed to fetch");
    expect(writeProblem("weird", "save").text).toBe("Something went wrong.");
  });
});
