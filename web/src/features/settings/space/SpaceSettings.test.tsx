// @vitest-environment jsdom
// Settings › Space's controlled sections: the repo list (fixed names, Remove rules, Add) and the
// Space fields (project keys as chips, the key prefix clash shown inline, the default repo select).
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  newRepoRow,
  repoRows,
  spaceForm,
  validateRepos,
  validateSpace,
  type RepoRow,
  type SpaceForm,
} from "./model";
import { RepoList } from "./RepoList";
import { SpaceFields } from "./SpaceFields";

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
    Reflect.set(proto.prototype, "value", value, el);
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
    );
  });
}

function byLabel(el: HTMLElement, text: string): HTMLElement | null {
  const label = [...el.querySelectorAll("label")].find((l) => l.textContent === text);
  return label ? document.getElementById(label.htmlFor) : null;
}

const META = { version: 1, updated_at: "2026-10-12T09:00:00Z", updated_by: null };
const ROWS = repoRows({
  ...META,
  repos: [
    {
      name: "agentos",
      url: "https://github.com/o/agentos.git",
      default_branch: "main",
      open_ticket_count: 2,
    },
    {
      name: "scratch",
      url: "git@github.com:o/scratch.git",
      default_branch: "main",
      open_ticket_count: 0,
    },
  ],
});

function repoList(rows: RepoRow[], onChange = vi.fn(), savedDefault: string | null = null) {
  const el = mount(
    <RepoList
      rows={rows}
      issues={validateRepos(rows)}
      readOnly={false}
      savedDefault={savedDefault}
      onChange={onChange}
    />,
  );
  return { el, onChange };
}

const button = (el: HTMLElement, label: string) =>
  el.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

describe("RepoList", () => {
  it("shows saved names as text, with their open ticket counts", () => {
    const { el } = repoList(ROWS);
    expect(el.textContent).toContain("agentos");
    expect(el.textContent).toContain("2 open tickets");
    expect(el.textContent).toContain("No open tickets");
    expect(byLabel(el, "Name")).toBeNull();
    // a non-https URL gets the runner hint, not an error
    expect(el.textContent).toContain("GitHub Actions runners clone over https:// only.");
  });

  it("disables Remove for a repo in use, with the reason as its title", () => {
    const { el, onChange } = repoList(ROWS, vi.fn(), "scratch");
    const agentos = button(el, "Remove agentos");
    const scratch = button(el, "Remove scratch");
    expect(agentos?.disabled).toBe(true);
    expect(agentos?.title).toBe("Can't remove: 2 open tickets use it.");
    expect(scratch?.disabled).toBe(true);
    expect(scratch?.title).toBe("Can't remove: it's the Space's default repo.");
    expect(el.textContent).toContain("Default");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("removes an unused repo", () => {
    const { el, onChange } = repoList(ROWS);
    act(() => {
      button(el, "Remove scratch")?.click();
    });
    expect(onChange).toHaveBeenCalledWith([ROWS[0]]);
  });

  it("adds a fresh row", () => {
    const { el, onChange } = repoList(ROWS);
    const add = [...el.querySelectorAll("button")].find((b) => b.textContent === "Add repo");
    act(() => {
      add?.click();
    });
    expect(onChange).toHaveBeenCalledWith([...ROWS, newRepoRow(ROWS)]);
  });

  it("a fresh row's name is editable (lowercased) and required", () => {
    const fresh = newRepoRow(ROWS);
    const next = repoList([...ROWS, fresh]);
    const name = byLabel(next.el, "Name");
    if (!(name instanceof HTMLInputElement)) throw new Error("no name input");
    expect(next.el.textContent).toContain("Required.");
    change(name, "My-Repo");
    expect(next.onChange).toHaveBeenLastCalledWith([...ROWS, { ...fresh, name: "my-repo" }]);
  });
});

describe("SpaceFields", () => {
  const saved = spaceForm({
    ...META,
    name: "AgentOS",
    key_prefix: "AOS",
    jira_projects: ["SCRUM"],
    default_repo: null,
    default_workflow_id: 1,
    claim_timeout_s: 900,
  });

  function fields(value: SpaceForm, onChange = vi.fn()) {
    const ctx = { jiraAuth: true, repoNames: ["agentos", "scratch"] };
    const el = mount(
      <SpaceFields
        value={value}
        issues={validateSpace(value, ctx)}
        readOnly={false}
        repoNames={ctx.repoNames}
        jiraAuth={ctx.jiraAuth}
        reposDirty={false}
        onChange={onChange}
      />,
    );
    return { el, onChange };
  }

  it("offers None and the saved repos as the default repo", () => {
    const { el, onChange } = fields(saved);
    const select = byLabel(el, "Default repo");
    if (!(select instanceof HTMLSelectElement)) throw new Error("no select");
    expect([...select.options].map((o) => o.textContent)).toEqual(["None", "agentos", "scratch"]);
    change(select, "scratch");
    expect(onChange).toHaveBeenLastCalledWith({ ...saved, default_repo: "scratch" });
  });

  it("adds a typed project key on Enter, uppercased, and removes one with ×", () => {
    const { el, onChange } = fields(saved);
    const input = byLabel(el, "Jira projects");
    if (!(input instanceof HTMLInputElement)) throw new Error("no keys input");
    change(input, "plat");
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith({ ...saved, jira_projects: ["SCRUM", "PLAT"] });
    act(() => {
      button(el, "Remove SCRUM")?.click();
    });
    expect(onChange).toHaveBeenLastCalledWith({ ...saved, jira_projects: [] });
  });

  it("shows a typing problem under the keys", () => {
    const { el, onChange } = fields(saved);
    const input = byLabel(el, "Jira projects");
    if (!(input instanceof HTMLInputElement)) throw new Error("no keys input");
    change(input, "scrum");
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onChange).not.toHaveBeenCalled();
    expect(el.textContent).toContain("SCRUM is already listed.");
  });

  it("explains a key prefix that clashes with a Jira project, inline", () => {
    const { el } = fields({ ...saved, key_prefix: "SCRUM" });
    const prefix = byLabel(el, "Key prefix");
    expect(prefix?.getAttribute("aria-invalid")).toBe("true");
    expect(el.textContent).toContain("SCRUM is also a Jira project.");
  });
});
