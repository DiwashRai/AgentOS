// @vitest-environment jsdom
// A mapping's Jira field: picked from Jira's list, or typed by hand (the admin's choice, or the
// only way when the list can't load); a typed id Jira knows takes Jira's name for it.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NO_ISSUES } from "../../../editor";
import { MappingCard, type FieldsState } from "./MappingCard";
import { newMapping, type JiraField, type MappingForm } from "./model";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const JIRA_FIELDS: JiraField[] = [
  { id: "fixVersions", name: "Fix versions", kind: "version" },
  { id: "customfield_10042", name: "Release train", kind: "option" },
];

let host: HTMLDivElement | null = null;
let root: Root | null = null;
let latest: MappingForm | null = null;

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  host?.remove();
  host = null;
  root = null;
  latest = null;
});

function Card({ fields }: { fields: FieldsState }) {
  const [row, setRow] = useState(() => newMapping("base_branch"));
  return (
    <MappingCard
      row={row}
      issues={NO_ISSUES}
      fields={fields}
      preview={vi.fn()}
      readOnly={false}
      onChange={(item) => {
        latest = item;
        setRow({ ...row, item });
      }}
    />
  );
}

function mount(fields: Partial<FieldsState>): HTMLDivElement {
  const el = document.createElement("div");
  document.body.append(el);
  host = el;
  const r = createRoot(el);
  root = r;
  const state: FieldsState = {
    data: undefined,
    pending: false,
    fetching: false,
    error: null,
    retry: () => undefined,
    ...fields,
  };
  act(() => {
    r.render(<Card fields={state} />);
  });
  return el;
}

/** The control a `<label>` with this text names, if any. */
function labelled(el: HTMLElement, name: string): HTMLElement | null {
  const label = [...el.querySelectorAll("label")].find((l) => l.textContent.trim() === name);
  return label ? document.getElementById(label.htmlFor) : null;
}

function button(el: HTMLElement, name: string): HTMLButtonElement | undefined {
  return [...el.querySelectorAll("button")].find((b) => b.textContent.trim() === name);
}

function click(el: HTMLElement, name: string) {
  const b = button(el, name);
  if (!b) throw new Error(`no button "${name}"`);
  act(() => {
    b.click();
  });
}

function type(input: HTMLElement | null, value: string) {
  if (!(input instanceof HTMLInputElement)) throw new Error("not an input");
  act(() => {
    Reflect.set(HTMLInputElement.prototype, "value", value, input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("MappingCard field picker", () => {
  it("switches between Jira's list and typing the id", () => {
    const el = mount({ data: JIRA_FIELDS });
    expect(labelled(el, "Jira field")).toBeInstanceOf(HTMLSelectElement);
    expect(labelled(el, "Jira field id")).toBeNull();

    click(el, "Enter the id by hand");
    expect(labelled(el, "Jira field")).toBeNull();
    type(labelled(el, "Jira field id"), "fixVersions");
    expect(latest).toMatchObject({ field: "fixVersions", field_name: "Fix versions" });

    click(el, "Pick from Jira's list");
    expect(labelled(el, "Jira field")).toBeInstanceOf(HTMLSelectElement);
    expect(labelled(el, "Jira field id")).toBeNull();
  });

  it("types the id when Jira's list can't load, with no way back to it", () => {
    const el = mount({ error: new Error("down") });

    expect(labelled(el, "Jira field")).toBeNull();
    expect(labelled(el, "Jira field id")).toBeInstanceOf(HTMLInputElement);
    expect(button(el, "Pick from Jira's list")).toBeUndefined();

    type(labelled(el, "Shown as"), "Train");
    expect(latest).toMatchObject({ field: "", field_name: "Train" });
  });
});
