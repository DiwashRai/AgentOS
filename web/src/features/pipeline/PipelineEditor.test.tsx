// @vitest-environment jsdom
// The editor's loop glue: a block is drawn around its stage rows with each row's role, "+ Loop"
// adds a loop to the emitted pipeline, and removing a block's edge stage shrinks it.
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { newStage, type Pipeline, type Stage } from "./model";
import { PipelineEditor } from "./PipelineEditor";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement | null = null;
let root: Root | null = null;

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  host?.remove();
  host = null;
  root = null;
  vi.restoreAllMocks();
});

function mount(node: ReactNode): HTMLDivElement {
  const el = document.createElement("div");
  document.body.append(el);
  host = el;
  const r = createRoot(el);
  root = r;
  act(() => {
    r.render(node);
  });
  return el;
}

function click(el: Element | null | undefined) {
  if (!(el instanceof HTMLElement)) throw new Error("nothing to click");
  act(() => {
    el.click();
  });
}

function button(el: HTMLElement, text: string): HTMLButtonElement {
  const b = [...el.querySelectorAll("button")].find((x) => x.textContent.trim() === text);
  if (!b) throw new Error(`no button "${text}"`);
  return b;
}

const stage = (id: string, over: Partial<Stage> = {}): Stage => ({
  ...newStage("agent", []),
  id,
  name: id,
  ...over,
});

const BASE: Pipeline = {
  stages: [
    stage("implement"),
    stage("lint", { kind: "shell", command: "make lint" }),
    stage("review", { check: true }),
    stage("open-pr"),
  ],
};

function Harness({ initial, spy }: { initial: Pipeline; spy: (p: Pipeline) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <PipelineEditor
      value={value}
      runtimes={["claude"]}
      space={{ runtime: "claude", model: null, effort: null }}
      onChange={(p) => {
        spy(p);
        setValue(p);
      }}
    />
  );
}

/** The ids of the stage rows inside the drawn block, in order. */
const blockIds = (el: HTMLElement) =>
  [...el.querySelectorAll("ol ol > li")].map(
    (li) => li.querySelector("button[aria-expanded]")?.textContent ?? "",
  );

describe("PipelineEditor loops", () => {
  it("draws no block without loops; + Loop adds one over a maker and the next check", () => {
    const spy = vi.fn();
    const el = mount(<Harness initial={BASE} spy={spy} />);
    expect(blockIds(el)).toEqual([]);
    click(button(el, "+ Loop"));
    const p = spy.mock.lastCall?.[0] as Pipeline;
    expect(p.loops).toEqual([{ start: "implement", end: "lint", max_rounds: 3 }]);
    expect(blockIds(el)).toHaveLength(2);
  });

  it("marks roles inside the block only", () => {
    const el = mount(
      <Harness
        initial={{ ...BASE, loops: [{ start: "implement", end: "review", max_rounds: 3 }] }}
        spy={() => undefined}
      />,
    );
    const rows = [...el.querySelectorAll("ol ol > li")];
    expect(rows).toHaveLength(3);
    const roleTitles = (li: Element) =>
      [...li.querySelectorAll("[title]")].map((t) => t.getAttribute("title") ?? "");
    const roles = rows.map((li) =>
      roleTitles(li).find((t) => t.startsWith("Maker") || t.startsWith("Check")),
    );
    expect(roles.map((t) => t?.split(":")[0])).toEqual(["Maker", "Check", "Check"]);
  });

  it("removing a loop and removing a block's last stage both reach the emitted pipeline", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const spy = vi.fn();
    const el = mount(
      <Harness
        initial={{ ...BASE, loops: [{ start: "implement", end: "review", max_rounds: 3 }] }}
        spy={spy}
      />,
    );
    // Open review (the block's last stage) and remove it: the block shrinks to implement..lint.
    const review = [...el.querySelectorAll("button[aria-expanded]")].find((b) =>
      b.textContent.startsWith("review"),
    );
    click(review);
    click(button(el, "Remove stage"));
    expect((spy.mock.lastCall?.[0] as Pipeline).loops).toEqual([
      { start: "implement", end: "lint", max_rounds: 3 },
    ]);
    click(button(el, "Remove loop"));
    expect((spy.mock.lastCall?.[0] as Pipeline).loops).toEqual([]);
    expect(blockIds(el)).toEqual([]);
  });
});
