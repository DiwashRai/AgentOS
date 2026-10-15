// @vitest-environment jsdom
// The list editor: Add focuses the new row, Remove asks first (unless the row is blank; Escape
// cancels) and then focuses the neighbour, a move keeps focus on the moved row and every row keeps
// its key, details expand and collapse, min / max turn Remove / Add off, and a preview shows only
// while its input equals what ran, from its latest run.
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

interface Options {
  min?: number;
  max?: number;
  /** Give each row details (a notes field) behind an expand toggle. */
  details?: boolean;
}

function Repos({ initial, min, max, details }: Options & { initial: readonly Row[] }) {
  const [rows, setRows] = useState(initial);
  return (
    <ListEditor
      name="Repos"
      rows={rows}
      ordered
      min={min}
      max={max}
      details={
        details === true
          ? (row) => <input aria-label={`Notes of repo ${row.item.name || "new"}`} />
          : undefined
      }
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

function mountRepos(names: readonly string[], options: Options = {}) {
  const initial = keyed(names.map((name) => ({ name })));
  latest = initial;
  return { el: render(<Repos initial={initial} {...options} />), initial };
}

/** A preview of the query typed in its box, with a Test button and its result. */
function PreviewTester({ run }: { run: (q: string) => Promise<string> }) {
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

/** A run whose calls each wait until the test finishes them, in any order. */
function deferredRun() {
  const finishers: ((value: string) => void)[] = [];
  const run = vi.fn(
    (q: string) =>
      new Promise<string>((resolve) => {
        finishers.push((v) => {
          resolve(`${v} for ${q}`);
        });
      }),
  );
  const finish = async (call: number, value: string) => {
    await act(async () => {
      finishers[call]?.(value);
      await Promise.resolve();
    });
  };
  return { run, finish };
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

function press(key: string) {
  const target = document.activeElement;
  if (!(target instanceof HTMLElement)) throw new Error("nothing focused");
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
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

  it("expands and collapses a row's details, and opens a newly added row", () => {
    const { el } = mountRepos(["a"], { details: true });
    const toggle = byRole(el, "button", "repo a details");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(query(el, "textbox", "Notes of repo a")).toBeUndefined();

    click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const panel = document.getElementById(toggle.getAttribute("aria-controls") ?? "");
    expect(panel?.contains(byRole(el, "textbox", "Notes of repo a"))).toBe(true);

    click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(query(el, "textbox", "Notes of repo a")).toBeUndefined();

    click(byRole(el, "button", "Add repo"));
    expect(byRole(el, "button", "repo new details").getAttribute("aria-expanded")).toBe("true");
    expect(query(el, "textbox", "Notes of repo new")).toBeDefined();
    expect(document.activeElement).toBe(byRole(el, "textbox", "Name of repo new"));
  });

  it("cancels a remove confirmation on Escape, focus back on the row's Remove", () => {
    const { el } = mountRepos(["a", "b"]);

    click(byRole(el, "button", "Remove repo a"));
    expect(byRole(el, "group", "Remove repo a?").contains(document.activeElement)).toBe(true);

    press("Escape");
    expect(query(el, "group", "Remove repo a?")).toBeUndefined();
    expect(names()).toEqual(["a", "b"]);
    expect(document.activeElement).toBe(byRole(el, "button", "Remove repo a"));
  });

  it("focuses the neighbour's Remove after a remove, and Add once the list is empty", () => {
    const { el } = mountRepos(["a", "b", "c"]);
    const removeConfirmed = (name: string) => {
      click(byRole(el, "button", `Remove repo ${name}`));
      click(byRole(byRole(el, "group", `Remove repo ${name}?`), "button", "Remove"));
    };

    removeConfirmed("b");
    expect(names()).toEqual(["a", "c"]);
    expect(document.activeElement).toBe(byRole(el, "button", "Remove repo c"));

    // The last row's neighbour is the one before it.
    removeConfirmed("c");
    expect(names()).toEqual(["a"]);
    expect(document.activeElement).toBe(byRole(el, "button", "Remove repo a"));

    removeConfirmed("a");
    expect(names()).toEqual([]);
    expect(document.activeElement).toBe(byRole(el, "button", "Add repo"));
  });

  it("shows a preview only while the input equals what ran", async () => {
    const { run, finish } = deferredRun();
    const el = render(<PreviewTester run={run} />);
    const input = byRole(el, "textbox", "Query");

    click(byRole(el, "button", "Test"));
    expect(run).toHaveBeenCalledWith("x");
    expect(query(el, "status", "Pending")).toBeDefined();

    type(input, "y");
    expect(query(el, "status", "Pending")).toBeUndefined();

    await finish(0, "2");
    expect(query(el, "status", "Result")).toBeUndefined();

    type(input, "x ");
    expect(byRole(el, "status", "Result").textContent).toBe("2 for x");
  });

  it("counts only the latest preview run, even when an older one finishes last", async () => {
    const { run, finish } = deferredRun();
    const el = render(<PreviewTester run={run} />);

    click(byRole(el, "button", "Test"));
    click(byRole(el, "button", "Test"));
    expect(run).toHaveBeenCalledTimes(2);

    await finish(1, "second");
    expect(byRole(el, "status", "Result").textContent).toBe("second for x");

    await finish(0, "first");
    expect(byRole(el, "status", "Result").textContent).toBe("second for x");
  });
});
