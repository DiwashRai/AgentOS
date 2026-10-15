import { describe, expect, it } from "vitest";

import { trapIndex } from "./model";
import { withoutParam, withParam } from "./params";

describe("trapIndex", () => {
  it("enters at the first (Tab) or last (Shift+Tab) from the panel", () => {
    expect(trapIndex(-1, 4, false)).toBe(0);
    expect(trapIndex(-1, 4, true)).toBe(3);
  });
  it("wraps at the ends and leaves the middle to the browser", () => {
    expect(trapIndex(3, 4, false)).toBe(0);
    expect(trapIndex(0, 4, true)).toBe(3);
    expect(trapIndex(1, 4, false)).toBeNull();
    expect(trapIndex(2, 4, true)).toBeNull();
  });
  it("keeps focus on the panel when nothing inside is focusable", () => {
    expect(trapIndex(-1, 0, false)).toBe(-1);
  });
});

it("changes one search param and keeps the rest", () => {
  const p = new URLSearchParams("f=blocked&peek=AOS-1");
  expect(withoutParam(p, "peek").toString()).toBe("f=blocked");
  expect(withParam(p, "peek", "AOS-2").toString()).toBe("f=blocked&peek=AOS-2");
  expect(p.toString()).toBe("f=blocked&peek=AOS-1");
});
