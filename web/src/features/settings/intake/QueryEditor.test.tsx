// @vitest-environment jsdom
// The intake query editor: edits go out as a new list, Add query appends a blank new query, and
// "Test query" shows the draft JQL's result under its row until that JQL is edited.
import { QueryClient, QueryClientProvider, useMutation } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IntakeForm, IntakePreview } from "./model";
import { QueryEditor } from "./QueryEditor";

const previewFn = vi.fn<(body: { jql: string }) => Promise<IntakePreview>>();

vi.mock("../../../api/queries", () => ({
  usePreviewIntake: () => useMutation({ mutationFn: (body: { jql: string }) => previewFn(body) }),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const START: IntakeForm = [
  { key: "id:ready", id: "ready", name: "Ready", jql: "project = PLAT" },
  { key: "id:bugs", id: "bugs", name: "Bugs", jql: "type = Bug" },
];

let host: HTMLDivElement | null = null;
let root: Root | null = null;
let latest: IntakeForm = START;

beforeEach(() => {
  previewFn.mockReset();
  latest = START;
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  host?.remove();
  host = null;
  root = null;
});

function Harness({ initial }: { initial: IntakeForm }) {
  const [value, setValue] = useState(initial);
  return (
    <QueryEditor
      value={value}
      readOnly={false}
      onChange={(next) => {
        latest = next;
        setValue(next);
      }}
    />
  );
}

function mount(initial: IntakeForm = START): HTMLDivElement {
  const el = document.createElement("div");
  document.body.append(el);
  host = el;
  const r = createRoot(el);
  root = r;
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  act(() => {
    r.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Harness initial={initial} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  return el;
}

function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
  act(() => {
    Reflect.set(proto.prototype, "value", value, el);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function button(el: HTMLElement, name: string): HTMLButtonElement {
  const b = [...el.querySelectorAll("button")].find(
    (x) => (x.getAttribute("aria-label") ?? x.textContent) === name,
  );
  if (!b) throw new Error(`no button ${name}`);
  return b;
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const textareas = (el: HTMLElement) => [...el.querySelectorAll("textarea")];

describe("QueryEditor", () => {
  it("edits a name, adds a blank new query, and moves rows", () => {
    const el = mount();
    const name = el.querySelector("input");
    if (!name) throw new Error("no name input");
    type(name, "Ready now");
    expect(latest[0]?.name).toBe("Ready now");

    act(() => {
      button(el, "+ Add query").click();
    });
    expect(latest).toHaveLength(3);
    expect(latest[2]).toEqual({ key: "new:1", id: null, name: "", jql: "" });
    // A blank query can't be tested yet.
    expect(button(el, "Test query 3").disabled).toBe(true);

    act(() => {
      button(el, "Move Bugs up").click();
    });
    expect(latest.map((r) => r.id)).toEqual(["bugs", "ready", null]);
  });

  it("shows the test result under its row until that JQL is edited", async () => {
    previewFn.mockResolvedValue({
      total: 7,
      error: null,
      items: ["PLAT-1", "PLAT-2"].map((key) => ({
        key,
        title: `Title ${key}`,
        type: "feature" as const,
        priority: "high" as const,
        status: "To Do",
        assignee: null,
        updated_at: "2026-10-12T08:00:00Z",
        url: `https://jira.example/browse/${key}`,
      })),
    });
    const el = mount();
    act(() => {
      button(el, "Test Ready").click();
    });
    await settle();
    expect(previewFn).toHaveBeenCalledWith({ jql: "project = PLAT" });

    const rows = [...el.querySelectorAll("li")].filter((li) => li.querySelector("textarea"));
    expect(rows[0]?.textContent).toContain("7 to take in");
    expect(rows[0]?.textContent).toContain("PLAT-1");
    expect(rows[0]?.textContent).toContain("Title PLAT-2");
    expect(rows[1]?.textContent).not.toContain("to take in");

    const jql = textareas(el)[0];
    if (!jql) throw new Error("no jql");
    type(jql, "project = PLAT AND labels = agent");
    expect(el.textContent).not.toContain("7 to take in");
  });

  it("shows Jira's error, or Nothing to take in", async () => {
    previewFn.mockResolvedValueOnce({ total: 0, error: "Field 'nope' does not exist", items: [] });
    previewFn.mockResolvedValueOnce({ total: 0, error: null, items: [] });
    const el = mount();
    act(() => {
      button(el, "Test Ready").click();
      button(el, "Test Bugs").click();
    });
    await settle();
    expect(el.textContent).toContain("Field 'nope' does not exist");
    expect(el.textContent).toContain("Nothing to take in");
  });
});
