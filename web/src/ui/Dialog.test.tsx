// @vitest-environment jsdom
// The Dialog's keys and focus: Tab wraps inside the panel, Escape closes the top dialog only (the
// last opened) and stops there, an inner control that stops Escape keeps it open, focus goes
// back to the opener or the dialog below, and closing waits out `exitMs`.
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { Dialog } from "./Dialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement | null = null;
let root: Root | null = null;

beforeAll(() => {
  // jsdom has no layout, so every element reports no boxes; give each one box so the trap
  // counts the panel's controls as drawn.
  vi.spyOn(Element.prototype, "getClientRects").mockImplementation(() => {
    const rect = document.body.getBoundingClientRect();
    return Object.assign([rect], { item: (i: number) => (i === 0 ? rect : null) });
  });
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  host?.remove();
  host = null;
  root = null;
  document.body.replaceChildren();
});

function render(ui: ReactNode) {
  if (!host) {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  }
  const r = root;
  act(() => {
    r?.render(ui);
  });
}

function dialog(name: string): HTMLElement {
  const found = [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(
    (el) => el.getAttribute("aria-label") === name,
  );
  if (!found) throw new Error(`no dialog ${name}`);
  return found;
}

function button(name: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find(
    (el) => (el.getAttribute("aria-label") ?? el.textContent) === name,
  );
  if (!found) throw new Error(`no button ${name}`);
  return found;
}

function press(el: Element, key: string, shiftKey = false) {
  act(() => {
    el.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, bubbles: true }));
  });
}

function blur() {
  act(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
}

function Form({ onClose, name = "Form" }: { onClose: () => void; name?: string }) {
  return (
    <Dialog label={name} onClose={onClose}>
      <button type="button">{`${name} first`}</button>
      <input
        aria-label={`${name} note`}
        onKeyDown={(e) => {
          if (e.key === "Escape") e.stopPropagation();
        }}
      />
      <button type="button">{`${name} last`}</button>
    </Dialog>
  );
}

describe("Dialog focus trap", () => {
  it("Tab from the last stop wraps to the first, Shift+Tab from the first to the last", () => {
    render(<Form onClose={vi.fn()} />);

    button("Form last").focus();
    press(button("Form last"), "Tab");
    expect(document.activeElement).toBe(button("Form first"));

    press(button("Form first"), "Tab", true);
    expect(document.activeElement).toBe(button("Form last"));
  });

  it("Tab from an inner element that is not a stop wraps only past an end", () => {
    render(
      <Dialog label="Notes" onClose={vi.fn()}>
        <div role="note" aria-label="Start" tabIndex={-1} />
        <button type="button">Notes first</button>
        <div role="note" aria-label="Middle" tabIndex={-1} />
        <button type="button">Notes last</button>
        <div role="note" aria-label="End" tabIndex={-1} />
      </Dialog>,
    );
    const note = (name: string) => {
      const found = document.querySelector<HTMLElement>(`[role="note"][aria-label="${name}"]`);
      if (!found) throw new Error(`no note ${name}`);
      return found;
    };
    const tab = (from: string, shiftKey = false) => {
      note(from).focus();
      const e = new KeyboardEvent("keydown", {
        key: "Tab",
        shiftKey,
        bubbles: true,
        cancelable: true,
      });
      act(() => {
        note(from).dispatchEvent(e);
      });
      return e.defaultPrevented;
    };

    // Between the stops the browser's own move stays inside: the dialog leaves it alone.
    expect(tab("Middle")).toBe(false);
    expect(tab("Middle", true)).toBe(false);

    expect(tab("End")).toBe(true);
    expect(document.activeElement).toBe(button("Notes first"));
    expect(tab("Start", true)).toBe(true);
    expect(document.activeElement).toBe(button("Notes last"));
  });

  it("Tab with focus outside the panel lands inside it", () => {
    render(<Form onClose={vi.fn()} />);

    blur();
    expect(document.activeElement).toBe(document.body);
    press(document.body, "Tab");
    expect(document.activeElement).toBe(button("Form first"));

    blur();
    press(document.body, "Tab", true);
    expect(document.activeElement).toBe(button("Form last"));
  });
});

describe("Dialog Escape", () => {
  it("closes and stops there, from inside the panel and from body", () => {
    const outer = vi.fn();
    document.addEventListener("keydown", outer);
    const inside = vi.fn();
    render(<Form onClose={inside} />);
    press(button("Form first"), "Escape");

    const fromBody = vi.fn();
    render(null);
    render(<Form onClose={fromBody} />);
    blur();
    press(document.body, "Escape");
    document.removeEventListener("keydown", outer);

    expect(inside).toHaveBeenCalledOnce();
    expect(fromBody).toHaveBeenCalledOnce();
    expect(outer).not.toHaveBeenCalled();
  });

  it("stays open when an inner control stops Escape", () => {
    const onClose = vi.fn();
    render(<Form onClose={onClose} />);
    const note = dialog("Form").querySelector("input");
    if (!note) throw new Error("no note");
    press(note, "Escape");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("with two dialogs open, only the top one closes", () => {
    const below = vi.fn();
    const top = vi.fn();
    render(<Form name="Below" onClose={below} />);
    render(
      <>
        <Form name="Below" onClose={below} />
        <Form name="Top" onClose={top} />
      </>,
    );
    expect(dialog("Top").contains(document.activeElement)).toBe(true);

    press(button("Top first"), "Escape");
    press(button("Below first"), "Escape");
    expect(top).toHaveBeenCalledOnce();
    expect(below).not.toHaveBeenCalled();

    render(<Form name="Below" onClose={below} />);
    expect(dialog("Below").contains(document.activeElement)).toBe(true);
    blur();
    press(document.body, "Escape");
    expect(below).toHaveBeenCalledOnce();
  });
});

describe("Dialog focus return", () => {
  it("moves focus in on open and back to the opener on close", () => {
    const opener = document.createElement("button");
    opener.textContent = "Open";
    document.body.append(opener);
    opener.focus();

    render(<Form onClose={vi.fn()} />);
    expect(dialog("Form").contains(document.activeElement)).toBe(true);

    render(null);
    expect(document.activeElement).toBe(opener);
  });

  it("goes to the dialog below when the opener is gone", () => {
    render(<Form name="Below" onClose={vi.fn()} />);
    const opener = document.createElement("button");
    opener.textContent = "Open";
    document.body.append(opener);
    opener.focus();

    render(
      <>
        <Form name="Below" onClose={vi.fn()} />
        <Form name="Top" onClose={vi.fn()} />
      </>,
    );
    opener.remove();
    render(<Form name="Below" onClose={vi.fn()} />);

    expect(document.activeElement).toBe(dialog("Below"));
  });

  it("keeps focus a child took on mount, and still returns it to the opener", () => {
    const opener = document.createElement("button");
    opener.textContent = "Open";
    document.body.append(opener);
    opener.focus();

    render(
      <Dialog label="Search" onClose={vi.fn()}>
        <button type="button">Search first</button>
        <input aria-label="Query" ref={(el) => el?.focus()} />
      </Dialog>,
    );
    expect(document.activeElement).toBe(dialog("Search").querySelector("input"));

    render(null);
    expect(document.activeElement).toBe(opener);
  });
});

describe("Dialog closing", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function reducedMotion(matches: boolean) {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches, media: query }));
  }

  function Animated({ onClose }: { onClose: () => void }) {
    return (
      <Dialog label="Animated" exitMs={260} onClose={onClose}>
        {(close) => (
          <button type="button" onClick={close}>
            Done
          </button>
        )}
      </Dialog>
    );
  }

  it("stays rendered for exitMs, then calls onClose", () => {
    vi.useFakeTimers();
    reducedMotion(false);
    const onClose = vi.fn();
    render(<Animated onClose={onClose} />);

    act(() => {
      button("Done").click();
    });
    act(() => {
      vi.advanceTimersByTime(259);
    });
    expect(onClose).not.toHaveBeenCalled();
    expect(dialog("Animated").isConnected).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes at once under reduced motion", () => {
    reducedMotion(true);
    const onClose = vi.fn();
    render(<Animated onClose={onClose} />);

    act(() => {
      button("Done").click();
    });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("a scrim click closes it", () => {
    const onClose = vi.fn();
    render(
      <Dialog label="Peek" scrimLabel="Close peek" onClose={onClose}>
        <button type="button">Peek first</button>
      </Dialog>,
    );

    act(() => {
      button("Close peek").click();
    });
    expect(onClose).toHaveBeenCalledOnce();
  });
});
