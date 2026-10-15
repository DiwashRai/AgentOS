import { describe, expect, it } from "vitest";

import { meterOfText } from "./meter";

describe("meterOfText", () => {
  it("reads k/n, n/n when finished and 0/n when queued", () => {
    expect(meterOfText(3, 5, "running")).toBe("3/5");
    expect(meterOfText(3, 5, "finished")).toBe("5/5");
    expect(meterOfText(3, 5, "queued")).toBe("0/5");
  });

  it("clamps k into range", () => {
    expect(meterOfText(9, 2, "running")).toBe("2/2");
    expect(meterOfText(-1, 2, "running")).toBe("0/2");
  });
});
