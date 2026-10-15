// @vitest-environment jsdom
// The Space page's own inputs: the project keys commit what was typed (upper-cased) on Enter, a
// comma or leaving the field, and × removes the key at its position; a new repo's name is
// lower-cased as it is typed.
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { NO_ISSUES } from "../../../editor";
import type { RepoRow, ReposForm } from "./model";
import { ProjectKeys } from "./ProjectKeys";
import { RepoList } from "./RepoList";

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
});

function render(node: ReactNode): HTMLDivElement {
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

/** Buttons by accessible name (aria-label, else text), in document order. */
function buttons(el: HTMLElement, name: string): HTMLButtonElement[] {
  return [...el.querySelectorAll("button")].filter(
    (b) => (b.getAttribute("aria-label") ?? b.textContent.trim()) === name,
  );
}

function button(el: HTMLElement, name: string): HTMLButtonElement {
  const found = buttons(el, name)[0];
  if (!found) throw new Error(`no button "${name}"`);
  return found;
}

/** The control a `<label>` with this text names. */
function labelled(el: HTMLElement, name: string): HTMLInputElement {
  const label = [...el.querySelectorAll("label")].find((l) => l.textContent.trim() === name);
  const control = label ? document.getElementById(label.htmlFor) : null;
  if (!(control instanceof HTMLInputElement)) throw new Error(`no field "${name}"`);
  return control;
}

function type(input: HTMLInputElement, value: string) {
  act(() => {
    Reflect.set(HTMLInputElement.prototype, "value", value, input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function press(input: HTMLInputElement, key: string) {
  act(() => {
    input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  });
}

function click(b: HTMLElement) {
  act(() => {
    b.click();
  });
}

describe("ProjectKeys", () => {
  let keys: string[] = [];

  function Keys({ initial }: { initial: string[] }) {
    const [value, setValue] = useState(initial);
    return (
      <>
        <label htmlFor="keys">Project keys</label>
        <ProjectKeys
          id="keys"
          describedBy={undefined}
          invalid={false}
          value={value}
          flagged={() => false}
          readOnly={false}
          onChange={(next) => {
            keys = next;
            setValue(next);
          }}
        />
      </>
    );
  }

  function mount(initial: string[]) {
    keys = initial;
    const el = render(<Keys initial={initial} />);
    return { el, input: labelled(el, "Project keys") };
  }

  it("commits the typed key, upper-cased, on Enter, a comma or leaving the field", () => {
    const { input } = mount([]);

    type(input, "scrum");
    press(input, "Enter");
    expect(keys).toEqual(["SCRUM"]);
    expect(input.value).toBe("");

    type(input, "plat");
    press(input, ",");
    expect(keys).toEqual(["SCRUM", "PLAT"]);

    act(() => {
      input.focus();
    });
    type(input, "ops");
    act(() => {
      input.blur();
    });
    expect(keys).toEqual(["SCRUM", "PLAT", "OPS"]);
  });

  it("removes the key at the × that was pressed, even when a key repeats", () => {
    const { el } = mount(["A", "B", "A"]);

    const second = buttons(el, "Remove A")[1];
    if (!second) throw new Error("no second Remove A");
    click(second);
    expect(keys).toEqual(["A", "B"]);

    click(button(el, "Remove A"));
    expect(keys).toEqual(["B"]);
  });
});

describe("RepoList", () => {
  let repos: RepoRow[] = [];

  function Repos() {
    const [value, setValue] = useState<ReposForm>({ repos: [] });
    return (
      <RepoList
        value={value}
        issues={NO_ISSUES}
        readOnly={false}
        openCounts={{}}
        savedDefault={null}
        onChange={(next) => {
          repos = next.repos;
          setValue(next);
        }}
      />
    );
  }

  it("lower-cases a new repo's name as it is typed", () => {
    const el = render(<Repos />);

    click(button(el, "Add repo"));
    type(labelled(el, "Name"), "AgentOS");

    expect(repos.map((r) => r.name)).toEqual(["agentos"]);
    expect(labelled(el, "Name").value).toBe("agentos");
  });
});
