// @vitest-environment jsdom
// The Space defaults fields (runtime, model, effort) send null (never "") when cleared, and stage
// fields show the Space defaults as their unset labels.
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DefaultsSection } from "./DefaultsSection";
import { newStage, type Defaults } from "./model";
import { StageRow, type StageRowProps } from "./StageRow";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RT = ["claude", "copilot"];
let host: HTMLDivElement | null = null;
let root: Root | null = null;

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  host?.remove();
  host = null;
  root = null;
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

/** Set a controlled input/select's value the way a user would, so React sees the change. */
function change(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement : HTMLInputElement;
  act(() => {
    // The prototype's setter, so React's value tracker notices the change.
    Reflect.set(proto.prototype, "value", value, el);
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
    );
  });
}

/** The field a `<label>` with exactly this text points at. */
function byLabel<T extends HTMLElement>(el: HTMLElement, text: string, ctor: new () => T): T {
  const label = [...el.querySelectorAll("label")].find((l) => l.textContent === text);
  const found = document.getElementById(label?.htmlFor ?? "");
  if (!(found instanceof ctor)) throw new Error(`no field "${text}"`);
  return found;
}

function defaults(value: Defaults, onChange: (d: Defaults) => void) {
  return mount(
    <DefaultsSection
      value={value}
      runtimes={RT}
      serverRuntime="claude"
      issues={{}}
      readOnly={false}
      onChange={onChange}
    />,
  );
}

describe("DefaultsSection", () => {
  it("shows the server runtime as the unset option and the CLI's default as the placeholders", () => {
    const el = defaults({}, () => undefined);
    const options = [...el.querySelectorAll("option")].map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["", "Server default (claude)"],
      ["claude", "claude"],
      ["copilot", "copilot"],
    ]);
    expect(byLabel(el, "Model", HTMLInputElement).placeholder).toBe("The CLI's default");
    expect(byLabel(el, "Effort", HTMLInputElement).placeholder).toBe("The CLI's default");
    expect(byLabel(el, "Runtime", HTMLSelectElement).value).toBe("");
  });

  it("still names the server runtime while the template sets one", () => {
    const el = defaults({ runtime: "copilot", model: null }, () => undefined);
    expect(el.querySelector("option")?.textContent).toBe("Server default (claude)");
    expect(byLabel(el, "Runtime", HTMLSelectElement).value).toBe("copilot");
  });

  it("clearing the model or effort, or picking the server default, sends null", () => {
    const onChange = vi.fn();
    const el = defaults({ runtime: "copilot", model: "gpt-5", effort: "high" }, onChange);
    const model = byLabel(el, "Model", HTMLInputElement);
    const effort = byLabel(el, "Effort", HTMLInputElement);
    const select = byLabel(el, "Runtime", HTMLSelectElement);
    expect(effort.value).toBe("high");
    change(model, "");
    expect(onChange).toHaveBeenLastCalledWith({ runtime: "copilot", model: null, effort: "high" });
    change(select, "");
    expect(onChange).toHaveBeenLastCalledWith({ runtime: null, model: "gpt-5", effort: "high" });
    change(effort, " ");
    expect(onChange).toHaveBeenLastCalledWith({ runtime: "copilot", model: "gpt-5", effort: null });
    change(effort, "xhigh");
    expect(onChange).toHaveBeenLastCalledWith({
      runtime: "copilot",
      model: "gpt-5",
      effort: "xhigh",
    });
  });

  it("shows an effort issue on the Effort field", () => {
    const el = mount(
      <DefaultsSection
        value={{ effort: "a b" }}
        runtimes={RT}
        serverRuntime="claude"
        issues={{ effort: "Letters, digits and . _ - only; at most 32." }}
        readOnly={false}
        onChange={() => undefined}
      />,
    );
    expect(byLabel(el, "Effort", HTMLInputElement).getAttribute("aria-invalid")).toBe("true");
    expect(el.textContent).toContain("Letters, digits and . _ - only; at most 32.");
  });
});

describe("StageRow Space defaults", () => {
  function row(over: Partial<StageRowProps["row"]["stage"]>, onPatch = vi.fn()) {
    const props: StageRowProps = {
      row: { uid: 1, fresh: false, stage: { ...newStage("agent", []), id: "plan", ...over } },
      index: 0,
      count: 1,
      issues: {},
      open: true,
      readOnly: false,
      runtimes: RT,
      space: { runtime: "claude", model: "claude-sonnet-4", effort: "high" },
      focusName: false,
      moveId: () => "m",
      onToggleOpen: () => undefined,
      onMove: () => undefined,
      onPatch,
      onSetId: () => undefined,
      onRemove: () => undefined,
    };
    return { el: mount(<StageRow {...props} />), onPatch };
  }

  const modelInput = (el: HTMLElement) => byLabel(el, "Model", HTMLInputElement);
  const effortInput = (el: HTMLElement) => byLabel(el, "Effort", HTMLInputElement);
  const runtimeSelect = (el: HTMLElement) => byLabel(el, "Runtime", HTMLSelectElement);

  it("labels unset runtime, model and effort with the Space defaults", () => {
    const { el } = row({});
    expect(runtimeSelect(el).querySelector("option")?.textContent).toBe("Space default (claude)");
    expect(modelInput(el).placeholder).toBe("Space default (claude-sonnet-4)");
    expect(effortInput(el).placeholder).toBe("Space default (high)");
  });

  it("a stage on another runtime gets the CLI's default model and effort", () => {
    const { el } = row({ runtime: "copilot" });
    expect(modelInput(el).placeholder).toBe("The CLI's default");
    expect(effortInput(el).placeholder).toBe("The CLI's default");
  });

  it("clearing the runtime, model or effort patches null; typing an effort patches it", () => {
    const { el, onPatch } = row({ runtime: "copilot", model: "gpt-5", effort: "high" });
    expect(effortInput(el).value).toBe("high");
    change(runtimeSelect(el), "");
    expect(onPatch).toHaveBeenLastCalledWith({ runtime: null });
    change(modelInput(el), "");
    expect(onPatch).toHaveBeenLastCalledWith({ model: null });
    change(effortInput(el), "");
    expect(onPatch).toHaveBeenLastCalledWith({ effort: null });
    change(effortInput(el), " low ");
    expect(onPatch).toHaveBeenLastCalledWith({ effort: "low" });
  });

  it("a shell stage has no effort field", () => {
    const { el } = row({ kind: "shell", command: "make" });
    expect(() => effortInput(el)).toThrow();
  });
});
