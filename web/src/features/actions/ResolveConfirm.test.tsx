// @vitest-environment jsdom
// The Complete / Abandon confirm: Enter confirms with the note, Shift+Enter doesn't, Esc cancels
// without reaching outer Esc handlers (the peek).
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NOTE_MAX, ResolveConfirm, type ResolveConfirmProps } from "./ResolveConfirm";

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

function props(over: Partial<ResolveConfirmProps> = {}): ResolveConfirmProps {
  return {
    outcome: "abandoned",
    pending: false,
    error: null,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    ...over,
  };
}

function mount(p: ResolveConfirmProps) {
  host = document.createElement("div");
  document.body.append(host);
  const r = createRoot(host);
  root = r;
  act(() => {
    r.render(<ResolveConfirm {...p} />);
  });
  const textarea = host.querySelector("textarea");
  if (!textarea) throw new Error("no note field");
  return textarea;
}

function type(el: HTMLTextAreaElement, value: string) {
  // React tracks the instance's value, so set it through the prototype for onChange to fire.
  // eslint-disable-next-line @typescript-eslint/unbound-method -- called with .call(el) below
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function key(el: Element, k: string, shiftKey = false) {
  act(() => {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: k, shiftKey, bubbles: true }));
  });
}

describe("ResolveConfirm markup", () => {
  it("abandon: the red confirm, an optional capped note", () => {
    const html = renderToStaticMarkup(<ResolveConfirm {...props()} />);
    expect(html).toContain('aria-label="Close as abandoned"');
    expect(html).toContain('placeholder="Add a note (optional)"');
    expect(html).toContain(`maxLength="${String(NOTE_MAX)}"`);
    expect(html).toMatch(/type="submit"[^>]*>Abandon<\/button>/);
  });

  it("complete: Complete, and the busy word while pending", () => {
    const html = renderToStaticMarkup(<ResolveConfirm {...props({ outcome: "completed" })} />);
    expect(html).toContain('aria-label="Close as complete"');
    expect(html).toMatch(/type="submit"[^>]*>Complete<\/button>/);
    const busy = renderToStaticMarkup(
      <ResolveConfirm {...props({ outcome: "completed", pending: true })} />,
    );
    expect(busy).toMatch(/disabled=""[^>]*>Completing…<\/button>/);
  });

  it("shows an error inline", () => {
    const html = renderToStaticMarkup(
      <ResolveConfirm {...props({ error: "A run is already active" })} />,
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("A run is already active");
  });
});

describe("ResolveConfirm keys", () => {
  it("Enter confirms with the note; Shift+Enter does not", () => {
    const p = props();
    const note = mount(p);
    type(note, "Superseded by AOS-9");
    key(note, "Enter", true);
    expect(p.onSubmit).not.toHaveBeenCalled();
    key(note, "Enter");
    expect(p.onSubmit).toHaveBeenCalledExactlyOnceWith("Superseded by AOS-9");
  });

  it("Enter does nothing while the resolve is pending", () => {
    const p = props({ pending: true });
    key(mount(p), "Enter");
    expect(p.onSubmit).not.toHaveBeenCalled();
  });

  it("Esc cancels and stops there", () => {
    const p = props();
    const outer = vi.fn();
    window.addEventListener("keydown", outer);
    key(mount(p), "Escape");
    window.removeEventListener("keydown", outer);
    expect(p.onCancel).toHaveBeenCalledOnce();
    expect(outer).not.toHaveBeenCalled();
  });
});
