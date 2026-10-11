// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { isInnerControl } from "./usePeek";

describe("isInnerControl", () => {
  const row = document.createElement("article");
  row.innerHTML =
    '<span id="t">Title</span><div><button id="b"><b id="bi">Approve</b></button></div>';
  const byId = (id: string) => row.querySelector(`#${id}`);

  it("treats a click inside a button as the button's", () => {
    expect(isInnerControl(byId("b"), row)).toBe(true);
    expect(isInnerControl(byId("bi"), row)).toBe(true);
  });
  it("treats a click on plain row content as the row's", () => {
    expect(isInnerControl(byId("t"), row)).toBe(false);
    expect(isInnerControl(row, row)).toBe(false);
    expect(isInnerControl(null, row)).toBe(false);
  });
});
