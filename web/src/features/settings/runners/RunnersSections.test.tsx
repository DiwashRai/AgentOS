// @vitest-environment jsdom
// The runtimes editor (summary rows, the server-default badge, one argument per line, the command
// preview, add/remove) and the runner section's launcher-dependent notes.
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { rowsFrom, validateRuntimes, type RunnerForm, type RuntimeRow } from "./model";
import { RunnerSection } from "./RunnerSection";
import { RuntimesEditor } from "./RuntimesEditor";

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

function type(el: HTMLTextAreaElement | HTMLInputElement | HTMLSelectElement, value: string) {
  const proto =
    el instanceof HTMLSelectElement
      ? HTMLSelectElement
      : el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement
        : HTMLInputElement;
  act(() => {
    Reflect.set(proto.prototype, "value", value, el);
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
    );
  });
}

function click(el: Element | null | undefined) {
  if (!(el instanceof HTMLElement)) throw new Error("nothing to click");
  act(() => {
    el.click();
  });
}

function byLabel<T extends HTMLElement>(el: HTMLElement, text: string, ctor: new () => T): T {
  const label = [...el.querySelectorAll("label")].find((l) => l.textContent === text);
  const found = document.getElementById(label?.htmlFor ?? "");
  if (!(found instanceof ctor)) throw new Error(`no field "${text}"`);
  return found;
}

function button(el: HTMLElement, text: string): HTMLButtonElement {
  const b = [...el.querySelectorAll("button")].find((x) => x.textContent.includes(text));
  if (!b) throw new Error(`no button "${text}"`);
  return b;
}

const SEED = rowsFrom([
  {
    name: "claude",
    argv: ["claude", "-p"],
    model_args: ["--model", "{model}"],
    agent_args: ["--agent", "{agent}"],
    prompt_via: "stdin",
    prompt_args: [],
  },
  {
    name: "copilot",
    argv: ["copilot"],
    model_args: [],
    agent_args: [],
    prompt_via: "arg",
    prompt_args: ["-p", "{prompt}"],
  },
]);

function Harness({ initial, spy }: { initial: RuntimeRow[]; spy: (rows: RuntimeRow[]) => void }) {
  const [rows, setRows] = useState(initial);
  return (
    <RuntimesEditor
      rows={rows}
      issues={validateRuntimes(rows)}
      readOnly={false}
      onChange={(next) => {
        spy(next);
        setRows(next);
      }}
    />
  );
}

describe("RuntimesEditor", () => {
  it("summarises each runtime and badges the first as the server default", () => {
    const el = mount(<Harness initial={SEED} spy={() => undefined} />);
    const items = [...el.querySelectorAll("li")];
    expect(items).toHaveLength(2);
    expect(items[0]?.textContent).toContain("Server default");
    expect(items[0]?.textContent).toContain("claude -p --model <model> --agent <agent>");
    expect(items[1]?.textContent).not.toContain("Server default");
  });

  it("moving a runtime up makes it the default", () => {
    const spy = vi.fn();
    const el = mount(<Harness initial={SEED} spy={spy} />);
    click(el.querySelector('[aria-label="Move copilot up"]'));
    const last = spy.mock.lastCall?.[0] as RuntimeRow[];
    expect(last.map((r) => r.entry.name)).toEqual(["copilot", "claude"]);
    expect(el.querySelector("li")?.textContent).toContain("Server default");
    expect(el.querySelector("li")?.textContent).toContain("copilot");
  });

  it("edits one argument per line, keeping a trailing newline while typing", () => {
    const spy = vi.fn();
    const el = mount(<Harness initial={SEED} spy={spy} />);
    click(el.querySelector("li button[aria-expanded]"));
    const cmd = byLabel(el, "Command", HTMLTextAreaElement);
    expect(cmd.value).toBe("claude\n-p");

    type(cmd, "claude\n-p\n");
    expect(cmd.value).toBe("claude\n-p\n"); // nothing changed yet, the newline stays
    expect(spy).not.toHaveBeenCalled();

    type(cmd, "claude\n-p\n--verbose");
    expect((spy.mock.lastCall?.[0] as RuntimeRow[])[0]?.entry.argv).toEqual([
      "claude",
      "-p",
      "--verbose",
    ]);
    const preview = el.querySelector('[aria-label="Command preview"]');
    expect(preview?.textContent).toContain("claude -p --verbose --model <model> --agent <agent>");
    expect(preview?.textContent).toContain("The prompt goes to stdin.");
  });

  it("shows prompt arguments only when the prompt is an argument, and flags a missing {prompt}", () => {
    const el = mount(<Harness initial={SEED} spy={() => undefined} />);
    click(el.querySelector("li button[aria-expanded]"));
    expect(() => byLabel(el, "Prompt arguments", HTMLTextAreaElement)).toThrow();

    type(byLabel(el, "Prompt", HTMLSelectElement), "arg");
    const args = byLabel(el, "Prompt arguments", HTMLTextAreaElement);
    expect(args.value).toBe("{prompt}");
    type(args, "-p");
    expect(args.getAttribute("aria-invalid")).toBe("true");
    expect(el.textContent).toContain("Needs a line that is exactly {prompt}.");
  });

  it("adds an empty runtime (open, with an issue) and removes one after confirming", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const spy = vi.fn();
    const el = mount(<Harness initial={SEED} spy={spy} />);
    click(button(el, "Add runtime"));
    const items = el.querySelectorAll("li");
    expect(items).toHaveLength(3);
    expect(items[2]?.textContent).toContain("runtime");
    expect(items[2]?.textContent).toContain("1 issue");
    expect(byLabel(el, "Name", HTMLInputElement).value).toBe("runtime");

    click(button(el, "Remove runtime"));
    expect(el.querySelectorAll("li")).toHaveLength(2);
  });

  it("can't remove the last runtime", () => {
    const el = mount(<Harness initial={SEED.slice(0, 1)} spy={() => undefined} />);
    click(el.querySelector("li button[aria-expanded]"));
    expect(button(el, "Remove runtime").disabled).toBe(true);
  });
});

describe("RunnerSection", () => {
  const FORM: RunnerForm = { github_repo: "", workflow: "agentos-worker.yml", ref: "main" };

  it("with the GitHub launcher, says runs fail until a repo is set and flags a missing token", () => {
    const el = mount(
      <RunnerSection
        settings={{ launcher: "github", token_set: false }}
        value={FORM}
        issues={{}}
        readOnly={false}
        onChange={() => undefined}
      />,
    );
    expect(el.textContent).toContain("GitHub Actions");
    expect(el.textContent).toContain("AGENTOS_GITHUB_TOKEN isn't set on the server");
    expect(el.textContent).toContain("runs fail at launch until it is");
    expect(el.textContent).toContain("changes apply to the next run, no restart");
  });

  it("with the local launcher, says the fields apply only to the GitHub launcher", () => {
    const onChange = vi.fn();
    const el = mount(
      <RunnerSection
        settings={{ launcher: "local", token_set: true }}
        value={FORM}
        issues={{}}
        readOnly={false}
        onChange={onChange}
      />,
    );
    expect(el.textContent).toContain("Local process");
    expect(el.textContent).toContain("GitHub token set");
    expect(el.textContent).toContain("apply only when it uses the GitHub launcher");
    expect(el.textContent).not.toContain("runs fail at launch");
    type(byLabel(el, "GitHub repo", HTMLInputElement), "acme/agentos");
    expect(onChange).toHaveBeenLastCalledWith({ ...FORM, github_repo: "acme/agentos" });
  });
});
