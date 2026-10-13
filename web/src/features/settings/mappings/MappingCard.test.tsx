// @vitest-environment jsdom
// A mapping card: "Add a mapping" without one; with one, edits go out as patches, Try it sends the
// unsaved rules and lights the rule the server says matched, and a failed field list keeps the
// saved field and says why.
import { QueryClient, QueryClientProvider, useMutation } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../api/client";
import { MappingCard, type FieldsState } from "./MappingCard";
import {
  formOf,
  TARGETS,
  validateMapping,
  type MappingDraft,
  type MappingPreview,
  type MappingPreviewRequest,
} from "./model";

const previewFn = vi.fn<(body: MappingPreviewRequest) => Promise<MappingPreview>>();

vi.mock("../../../api/queries", () => ({
  usePreviewMapping: () =>
    useMutation({ mutationFn: (body: MappingPreviewRequest) => previewFn(body) }),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function need<T>(x: T | undefined): T {
  if (x === undefined) throw new Error("missing fixture");
  return x;
}

const SPEC = need(TARGETS[0]);
const SAVED = need(
  formOf({
    mappings: [
      {
        target: "base_branch",
        source: "jira",
        field: "fixVersions",
        field_name: "Fix versions",
        rules: [
          { match: "version_1*", value: "App-1.Latest" },
          { match: "version_2*", value: "App-2.Latest" },
        ],
      },
    ],
  })[0],
);

const FIELDS_OK: FieldsState = {
  data: [{ id: "fixVersions", name: "Fix versions", kind: "version" }],
  pending: false,
  fetching: false,
  error: null,
  retry: () => undefined,
};

let host: HTMLDivElement | null = null;
let root: Root | null = null;
let latest: MappingDraft | undefined;
const added = vi.fn();

beforeEach(() => {
  previewFn.mockReset();
  added.mockReset();
  latest = SAVED;
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  host?.remove();
  host = null;
  root = null;
});

function Harness({ initial, fields }: { initial: MappingDraft | undefined; fields: FieldsState }) {
  const [draft, setDraft] = useState(initial);
  return (
    <MappingCard
      spec={SPEC}
      draft={draft}
      issues={draft && validateMapping(draft)}
      fields={fields}
      readOnly={false}
      onAdd={added}
      onRemove={() => undefined}
      onChange={(patch) => {
        if (!draft) return;
        const next = { ...draft, ...patch };
        latest = next;
        setDraft(next);
      }}
    />
  );
}

function mount(initial: MappingDraft | undefined, fields = FIELDS_OK): HTMLDivElement {
  const el = document.createElement("div");
  document.body.append(el);
  host = el;
  const r = createRoot(el);
  root = r;
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  act(() => {
    r.render(
      <QueryClientProvider client={client}>
        <Harness initial={initial} fields={fields} />
      </QueryClientProvider>,
    );
  });
  return el;
}

function type(el: HTMLInputElement, value: string) {
  act(() => {
    Reflect.set(HTMLInputElement.prototype, "value", value, el);
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

function input(el: HTMLElement, label: string): HTMLInputElement {
  const i = el.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!i) throw new Error(`no input ${label}`);
  return i;
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("MappingCard", () => {
  it("offers Add a mapping when there is none", () => {
    const el = mount(undefined);
    expect(el.textContent).toContain("start from their repo's default branch");
    act(() => {
      button(el, "Add a mapping").click();
    });
    expect(added).toHaveBeenCalledOnce();
  });

  it("edits a rule and moves it", () => {
    const el = mount(SAVED);
    type(input(el, "Rule 1: branch"), "App-1.Next");
    expect(latest?.rules[0]?.value).toBe("App-1.Next");
    act(() => {
      button(el, "Move rule 2 up").click();
    });
    expect(latest?.rules.map((r) => r.match)).toEqual(["version_2*", "version_1*"]);
  });

  it("tries the unsaved rules and lights the rule that matched", async () => {
    previewFn.mockResolvedValue({
      values: ["version_2.0"],
      value: "App-2.Latest",
      rule: 1,
      reason: null,
    });
    const el = mount(SAVED);
    type(input(el, "Jira values"), "version_2.0");
    act(() => {
      button(el, "Try it").click();
    });
    await settle();
    expect(previewFn).toHaveBeenCalledWith({
      mapping: {
        target: "base_branch",
        source: "jira",
        field: "fixVersions",
        field_name: "Fix versions",
        rules: [
          { match: "version_1*", value: "App-1.Latest" },
          { match: "version_2*", value: "App-2.Latest" },
        ],
      },
      values: ["version_2.0"],
    });
    expect(el.textContent).toContain("App-2.Latest · from rule 2");
    const lit = el.querySelectorAll("li[data-matched]");
    expect(lit).toHaveLength(1);
    expect(lit[0]?.querySelector("input")?.value).toBe("version_2*");

    // An edit to the rules hides the result (it no longer describes them).
    type(input(el, "Rule 1: branch"), "App-1.Next");
    expect(el.querySelectorAll("li[data-matched]")).toHaveLength(0);
  });

  it("shows the reason when the result is blank", async () => {
    previewFn.mockResolvedValue({
      values: ["version_3.0"],
      value: null,
      rule: null,
      reason: "No rule matches version_3.0",
    });
    const el = mount(SAVED);
    type(input(el, "Jira values"), "version_3.0");
    act(() => {
      button(el, "Try it").click();
    });
    await settle();
    expect(el.textContent).toContain("Blank · No rule matches version_3.0");
  });

  it("keeps the saved field when Jira's fields can't be listed", () => {
    const el = mount(SAVED, {
      ...FIELDS_OK,
      data: undefined,
      error: new ApiError(501, "not_implemented", "Jira isn't configured"),
    });
    const select = el.querySelector("select");
    expect(select?.value).toBe("fixVersions");
    expect(select?.disabled).toBe(true);
    expect(el.textContent).toContain("Jira import isn't configured");
    expect([...el.querySelectorAll("button")].some((b) => b.textContent === "Retry")).toBe(false);
  });
});
