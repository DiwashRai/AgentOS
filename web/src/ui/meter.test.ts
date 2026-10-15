import { describe, expect, it } from "vitest";
import { meterSegments, STRIP_LABEL_MAX, stripLabel, stripLabelled } from "./meter";

describe("meterSegments", () => {
  it("marks steps before k done and step k current while running", () => {
    expect(meterSegments(3, 5, "running")).toEqual(["done", "done", "cur", "up", "up"]);
  });
  it("fills every step when finished and none when queued", () => {
    expect(meterSegments(2, 3, "finished")).toEqual(["done", "done", "done"]);
    expect(meterSegments(0, 3, "queued")).toEqual(["up", "up", "up"]);
  });
  it("clamps k into range", () => {
    expect(meterSegments(9, 2, "running")).toEqual(["done", "cur"]);
    expect(meterSegments(-1, 2, "running")).toEqual(["up", "up"]);
    expect(meterSegments(1, 0, "running")).toEqual([]);
  });
});

describe("stripLabelled", () => {
  it("labels strip steps up to the cap only", () => {
    expect(stripLabelled(STRIP_LABEL_MAX)).toBe(true);
    expect(stripLabelled(STRIP_LABEL_MAX + 1)).toBe(false);
    expect(stripLabelled(13)).toBe(false);
  });
});

describe("stripLabel", () => {
  it("labels NOW, NEXT and STEP n", () => {
    const segs = meterSegments(2, 4, "running");
    expect(segs.map((_, i) => stripLabel(segs, i, "running"))).toEqual([
      "Step 1",
      "Now",
      "Next",
      "Step 4",
    ]);
    const done = meterSegments(4, 4, "finished");
    expect(done.map((_, i) => stripLabel(done, i, "finished"))).toEqual([
      "Step 1",
      "Step 2",
      "Step 3",
      "Step 4",
    ]);
  });
});
