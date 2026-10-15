// @vitest-environment jsdom
// The list editor: Add focuses the new row, Remove asks first (unless the row is blank), a move
// keeps focus on the moved row and every row keeps its key, min / max turn Remove / Add off, and a
// preview shows only while its input equals what ran.
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { keyed, newKey, type Keyed } from "./list";
import { ListEditor, usePreview } from "./ListEditor";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface Repo {
  name: string;
}
type Row = Keyed<Repo>;

let host: HTMLDivElement | null = null;
let root: Root | null = null;
let latest: readonly Row[] = [];

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

function Repos({ initial, min, max }: { initial: readonly Row[]; min?: number; max?: number }) {
  const [rows, setRows] = useState(initial);
  return (
    <ListEditor
      name="Repos"
      rows={rows}
      ordered
      min={min}
      max={max}
      addLabel="Add repo"
      label={(row) => `repo ${row.item.name || "new"}`}
      isBlank={(row) => row.item.name.trim() === ""}
      create={() => ({ key: newKey(), item: { name: "" } })}
      onChange={(next) => {
        latest = next;
        setRows(next);
      }}
    >
      {(row) => (
        <input
          aria-label={`Name of repo ${row.item.name || "new"}`}
          value={row.item.name}
          onChange={(e) => {
            const name = e.target.value;
            const next = rows.map((r) => (r.key === row.key ? { ...r, item: { name } } : r));
            latest = next;
            setRows(next);
          }}
        />
      )}
    </ListEditor>
  );
}

function mountRepos(names: readonly string[], limits: { min?: number; max?: number } = {}) {
  const initial = keyed(names.map((name) => ({ name })));
  latest = initial;
  return { el: render(<Repos initial={initial} {...limits} />), initial };
}

/** An element by role and accessible name (aria-label, else its text). */
function byRole(el: HTMLElement, role: string, name: string): HTMLElement {
  const found = query(el, role, name);
  if (!found) throw new Error(`no ${role} "${name}"`);
  return found;
}

function query(el: HTMLElement, role: string, name: string): HTMLElement | undefined {
  const selector =
    role === "button" ? "button" : role === "textbox" ? "input, textarea" : `[role="${role}"]`;
  return [...el.querySelectorAll<HTMLElement>(selector)].find(
    (x) => (x.getAttribute("aria-label") ?? x.textContent.trim()) === name,
  );
}

function click(b: HTMLElement) {
  act(() => {
    b.focus();
    b.click();
  });
}

function type(input: HTMLElement, value: string) {
  act(() => {
    Reflect.set(HTMLInputElement.prototype, "value", value, input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const names = () => latest.map((r) => r.item.name);

describe("ListEditor", () => {
  it("adds a row and focuses its first field", () => {
    const { el } = mountRepos(["a", "b"]);

    click(byRole(el, "button", "Add repo"));

    expect(names()).toEqual(["a", "b", ""]);
    expect(document.activeElement).toBe(byRole(el, "textbox", "Name of repo new"));
  });

  it("asks before removing a row, but not a blank one", () => {
    const { el } = mountRepos(["a", "b"]);

    click(byRole(el, "button", "Remove repo a"));
    expect(names()).toEqual(["a", "b"]);
    const ask = byRole(el, "group", "Remove repo a?");

    click(byRole(ask, "button", "Cancel"));
    expect(query(el, "group", "Remove repo a?")).toBeUndefined();
    expect(names()).toEqual(["a", "b"]);

    click(byRole(el, "button", "Remove repo a"));
    click(byRole(byRole(el, "group", "Remove repo a?"), "button", "Remove"));
    expect(names()).toEqual(["b"]);

    click(byRole(el, "button", "Add repo"));
    click(byRole(el, "button", "Remove repo new"));
    expect(query(el, "group", "Remove repo new?")).toBeUndefined();
    expect(names()).toEqual(["b"]);
  });

  it("keeps focus on the moved row and every row's key", () => {
    const { el, initial } = mountRepos(["a", "b", "c"]);
    const [a, b, c] = initial.map((r) => r.key);
    const input = byRole(el, "textbox", "Name of repo a");

    // React re-inserts the moved row's node, which drops its focus; the editor puts it back.
    click(byRole(el, "button", "Move repo a down"));
    expect(latest.map((r) => r.key)).toEqual([b, a, c]);
    expect(document.activeElement).toBe(byRole(el, "button", "Move repo a down"));

    // At the bottom, Down is off: focus stays on the row, on its other arrow.
    click(byRole(el, "button", "Move repo a down"));
    expect(latest.map((r) => r.key)).toEqual([b, c, a]);
    expect(document.activeElement).toBe(byRole(el, "button", "Move repo a up"));

    // The row kept its DOM (and so its state) across the moves.
    expect(byRole(el, "textbox", "Name of repo a")).toBe(input);
  });

  it("turns Add off at max and Remove off at min", () => {
    const { el } = mountRepos(["a", "b"], { min: 2, max: 2 });

    expect(byRole(el, "button", "Add repo")).toHaveProperty("disabled", true);
    expect(byRole(el, "button", "Remove repo a")).toHaveProperty("disabled", true);
    expect(byRole(el, "button", "Remove repo b")).toHaveProperty("disabled", true);
  });

  it("shows a preview only while the input equals what ran", async () => {
    let finish: (value: string) => void = () => undefined;
    const run = vi.fn(
      (q: string) =>
        new Promise<string>((resolve) => {
          finish = (v) => {
            resolve(`${v} for ${q}`);
          };
        }),
    );

    function Tester() {
      const [q, setQ] = useState("x");
      const preview = usePreview(run, q.trim());
      return (
        <>
          <input
            aria-label="Query"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
          />
          <button type="button" onClick={preview.run}>
            Test
          </button>
          {preview.pending ? <p role="status" aria-label="Pending" /> : null}
          {preview.result === undefined ? null : (
            <p role="status" aria-label="Result">
              {preview.result}
            </p>
          )}
        </>
      );
    }

    const el = render(<Tester />);
    const input = byRole(el, "textbox", "Query");

    click(byRole(el, "button", "Test"));
    expect(run).toHaveBeenCalledWith("x");
    expect(query(el, "status", "Pending")).toBeDefined();

    type(input, "y");
    expect(query(el, "status", "Pending")).toBeUndefined();

    await act(async () => {
      finish("2");
      await Promise.resolve();
    });
    expect(query(el, "status", "Result")).toBeUndefined();

    type(input, "x ");
    expect(byRole(el, "status", "Result").textContent).toBe("2 for x");
  });
});
