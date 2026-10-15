import { expect, it } from "vitest";

import { withoutParam, withParam } from "./params";

it("changes one search param and keeps the rest", () => {
  const p = new URLSearchParams("f=blocked&peek=AOS-1");
  expect(withoutParam(p, "peek").toString()).toBe("f=blocked");
  expect(withParam(p, "peek", "AOS-2").toString()).toBe("f=blocked&peek=AOS-2");
  expect(p.toString()).toBe("f=blocked&peek=AOS-1");
});
