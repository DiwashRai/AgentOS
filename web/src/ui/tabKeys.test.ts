import { describe, expect, it } from "vitest";
import { tabTarget } from "./tabKeys";

describe("tabTarget", () => {
  it("moves with arrows and wraps", () => {
    expect(tabTarget(0, "ArrowRight", 3)).toBe(1);
    expect(tabTarget(2, "ArrowRight", 3)).toBe(0);
    expect(tabTarget(0, "ArrowLeft", 3)).toBe(2);
  });
  it("jumps with Home and End", () => {
    expect(tabTarget(1, "Home", 3)).toBe(0);
    expect(tabTarget(1, "End", 3)).toBe(2);
  });
  it("ignores other keys and empty lists", () => {
    expect(tabTarget(1, "Enter", 3)).toBeNull();
    expect(tabTarget(0, "ArrowRight", 0)).toBeNull();
  });
});
