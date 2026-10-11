// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { isInnerControl } from "./usePeek";

describe("isInnerControl", () => {
  const row = document.createElement("article");
  row.innerHTML =
    '<span id="t">Title</span><div><button id="b"><b id="bi">Approve</b></button></div>' +
    '<form id="f"><span id="fi">Close as abandoned</span></form>';
  const byId = (id: string) => row.querySelector(`#${id}`);

  it("treats a click inside a button as the button's", () => {
    expect(isInnerControl(byId("b"), row)).toBe(true);
    expect(isInnerControl(byId("bi"), row)).toBe(true);
  });
  it("treats a click anywhere in an inline confirm form as the form's", () => {
    expect(isInnerControl(byId("f"), row)).toBe(true);
    expect(isInnerControl(byId("fi"), row)).toBe(true);
  });
  it("treats a click on plain row content as the row's", () => {
    expect(isInnerControl(byId("t"), row)).toBe(false);
    expect(isInnerControl(row, row)).toBe(false);
    expect(isInnerControl(null, row)).toBe(false);
  });
});
