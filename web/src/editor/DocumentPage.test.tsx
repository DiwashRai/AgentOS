// @vitest-environment jsdom
// A versioned document page: its loading / admins-only / error states, a save sending the value
// over its base version, a 422 putting every issue on its field at once, a 409 whose Reload keeps
// the draft and saves over the fresh version, validate-only issues after a pause, and one leave
// warning for a page with two documents. Real TanStack Query and react-router; a fake server.
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ValidationIssue } from "../api/client";
import { DocumentPage, DocumentSection } from "./DocumentPage";
import { Field, Input } from "./fields";
import { errorAt } from "./issues";
import {
  useDocument,
  VALIDATE_DELAY_MS,
  type DocumentRead,
  type DocumentSave,
  type DocumentState,
} from "./useDocument";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface Repo {
  name: string;
  url: string;
}
type Read = DocumentRead<Repo>;

const ADMINS_ONLY = "admins only";

/** The server's rules: a field rule each, and one about the document as a whole. */
function check(value: Repo): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!/^[a-z]+$/.test(value.name)) issues.push({ loc: ["value", "name"], message: "name rule" });
  if (!value.url.startsWith("https://"))
    issues.push({ loc: ["value", "url"], message: "url rule" });
  if (!value.url.includes(value.name)) issues.push({ loc: [], message: "document rule" });
  return issues;
}

function invalid(issues: ValidationIssue[]): ApiError {
  return new ApiError(422, "validation_error", "invalid", issues);
}

/** One document on a fake server; `overwrite` is another admin's save. */
function fakeServer(value: Repo) {
  let doc: Read = { value, version: 1, updated_at: "2026-10-12T09:00:00Z", updated_by: null };
  return {
    current: () => doc,
    overwrite(next: Repo) {
      doc = { ...doc, value: next, version: doc.version + 1 };
    },
    read: vi.fn(() => Promise.resolve(doc)),
    save: vi.fn((body: DocumentSave<Repo>) => {
      if (body.base_version !== doc.version) {
        return Promise.reject(new ApiError(409, "stale_version", "stale"));
      }
      const issues = check(body.value);
      if (issues.length > 0) return Promise.reject(invalid(issues));
      doc = { ...doc, value: body.value, version: doc.version + 1 };
      return Promise.resolve(doc);
    }),
    validate: vi.fn((body: { value: Repo }) => {
      const issues = check(body.value);
      return issues.length > 0 ? Promise.reject(invalid(issues)) : Promise.resolve(undefined);
    }),
  };
}
type Server = ReturnType<typeof fakeServer>;

/** A page's settings hooks: the read, and a save that puts its answer in the cache. */
function useRepoDocument(server: Server, key: string, live: boolean) {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: [key], queryFn: server.read });
  const save = useMutation({
    mutationFn: server.save,
    onSuccess: (data) => {
      qc.setQueryData([key], data);
    },
  });
  const validate = useMutation({ mutationFn: server.validate });
  return useDocument({ query, save, validate: live ? validate : undefined });
}

function RepoSection({ title, doc }: { title: string; doc: DocumentState<Read, Repo> }) {
  return (
    <DocumentSection title={title} document={doc}>
      {(form) => (
        <>
          <Field label={`${title} name`} error={errorAt(doc.issues, "name")}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={form.name}
                onChange={(e) => {
                  doc.edit({ ...form, name: e.target.value });
                }}
              />
            )}
          </Field>
          <Field label={`${title} URL`} error={errorAt(doc.issues, "url")}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={form.url}
                onChange={(e) => {
                  doc.edit({ ...form, url: e.target.value });
                }}
              />
            )}
          </Field>
        </>
      )}
    </DocumentSection>
  );
}

function OnePage({
  server,
  viewerRole,
  live,
}: {
  server: Server;
  viewerRole: string;
  live: boolean;
}) {
  const doc = useRepoDocument(server, "one", live);
  return (
    <DocumentPage
      viewer={{ role: viewerRole }}
      adminsOnly={ADMINS_ONLY}
      noun="repo"
      sources={[doc]}
    >
      {() => <RepoSection title="Repo" doc={doc} />}
    </DocumentPage>
  );
}

function TwoPage({ first, second }: { first: Server; second: Server }) {
  const a = useRepoDocument(first, "first", false);
  const b = useRepoDocument(second, "second", false);
  return (
    <DocumentPage viewer={{ role: "admin" }} adminsOnly={ADMINS_ONLY} noun="repos" sources={[a, b]}>
      {() => (
        <>
          <RepoSection title="First" doc={a} />
          <RepoSection title="Second" doc={b} />
        </>
      )}
    </DocumentPage>
  );
}

// ------------------------------------------------------------------ harness

let mounted: { root: Root; host: HTMLDivElement }[] = [];

afterEach(() => {
  act(() => {
    for (const m of mounted) m.root.unmount();
  });
  for (const m of mounted) m.host.remove();
  mounted = [];
  vi.restoreAllMocks();
});

function mount(page: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/settings", element: page },
      { path: "/other", element: <p>elsewhere</p> },
    ],
    { initialEntries: ["/settings"] },
  );
  const el = document.createElement("div");
  document.body.append(el);
  const r = createRoot(el);
  mounted.push({ root: r, host: el });
  act(() => {
    r.render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
  });
  return { el, router };
}

/** Let fetches, mutations and their re-renders finish. */
async function settle(ms = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
    for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** A text field by its label. */
function textbox(el: HTMLElement, name: string): HTMLInputElement {
  const label = [...el.querySelectorAll("label")].find((l) => l.textContent === name);
  const input = label ? document.getElementById(label.htmlFor) : null;
  if (!(input instanceof HTMLInputElement)) throw new Error(`no textbox "${name}"`);
  return input;
}

/** A field's accessible description (what aria-describedby points at). */
function description(input: HTMLElement): string | undefined {
  const id = input.getAttribute("aria-describedby");
  return id === null ? undefined : (document.getElementById(id)?.textContent ?? undefined);
}

function section(el: HTMLElement, name: string): HTMLElement {
  const found = el.querySelector<HTMLElement>(`section[aria-label="${name}"]`);
  if (!found) throw new Error(`no section "${name}"`);
  return found;
}

function button(el: HTMLElement, name: string): HTMLButtonElement {
  const found = [...el.querySelectorAll("button")].find(
    (b) => (b.getAttribute("aria-label") ?? b.textContent.trim()) === name,
  );
  if (!found) throw new Error(`no button "${name}"`);
  return found;
}

function roles(el: HTMLElement, role: string): string[] {
  return [...el.querySelectorAll(`[role="${role}"]`)].map((x) => x.textContent.trim());
}

function type(input: HTMLInputElement, value: string) {
  act(() => {
    Reflect.set(HTMLInputElement.prototype, "value", value, input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function click(b: HTMLElement) {
  act(() => {
    b.click();
  });
}

const VALID: Repo = { name: "a", url: "https://x/a" };

// ------------------------------------------------------------------ tests

describe("DocumentPage", () => {
  it("shows loading, then admins-only on a 403, and an error otherwise", async () => {
    const pending = fakeServer(VALID);
    pending.read.mockReturnValue(new Promise<Read>(() => undefined));
    let { el } = mount(<OnePage server={pending} viewerRole="admin" live={false} />);
    await settle();
    expect(roles(el, "status")).toHaveLength(1);
    expect(el.querySelector("input")).toBeNull();

    ({ el } = mount(<OnePage server={fakeServer(VALID)} viewerRole="member" live={false} />));
    await settle();
    expect(roles(el, "status")).toEqual([ADMINS_ONLY]);

    const forbidden = fakeServer(VALID);
    forbidden.read.mockRejectedValue(new ApiError(403, "forbidden", "no"));
    ({ el } = mount(<OnePage server={forbidden} viewerRole="admin" live={false} />));
    await settle();
    expect(roles(el, "status")).toEqual([ADMINS_ONLY]);

    const broken = fakeServer(VALID);
    broken.read.mockRejectedValue(new ApiError(502, "http_error", "down"));
    ({ el } = mount(<OnePage server={broken} viewerRole="admin" live={false} />));
    await settle();
    expect(roles(el, "alert")).toHaveLength(1);
    expect(el.querySelector("input")).toBeNull();
  });

  it("saves the value over the version it was loaded at", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live={false} />);
    await settle();

    type(textbox(el, "Repo URL"), "https://y/a");
    click(button(el, "Save"));
    await settle();

    expect(server.save).toHaveBeenCalledTimes(1);
    expect(server.save.mock.calls[0]?.[0]).toEqual({
      value: { name: "a", url: "https://y/a" },
      base_version: 1,
    });
    expect(server.current().version).toBe(2);
    expect(button(el, "Save").disabled).toBe(true);
    expect(textbox(el, "Repo URL").value).toBe("https://y/a");
  });

  it("puts every issue of a 422 on its field at once, the rest in the save bar", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live={false} />);
    await settle();
    const name = textbox(el, "Repo name");
    const url = textbox(el, "Repo URL");

    type(name, "A");
    type(url, "ftp://z");
    click(button(el, "Save"));
    await settle();

    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect(description(name)).toBe("name rule");
    expect(url.getAttribute("aria-invalid")).toBe("true");
    expect(description(url)).toBe("url rule");
    expect(roles(section(el, "Repo"), "alert")).toEqual(["document rule"]);
    expect(button(el, "Save").disabled).toBe(true);

    // An edit hides what no longer matches the draft; undoing it brings the issues back.
    type(name, "Ab");
    expect(name.getAttribute("aria-invalid")).toBe("false");
    expect(roles(section(el, "Repo"), "alert")).toEqual([]);
    type(name, "A");
    expect(description(name)).toBe("name rule");
  });

  it("reloads after a conflict keeping the draft, and the next save overwrites", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live={false} />);
    await settle();

    server.overwrite({ name: "a", url: "https://other/a" });
    type(textbox(el, "Repo URL"), "https://mine/a");
    click(button(el, "Save"));
    await settle();
    expect(server.save.mock.calls[0]?.[0].base_version).toBe(1);
    expect(server.current().value.url).toBe("https://other/a");

    click(button(section(el, "Repo"), "Reload"));
    await settle();
    expect(textbox(el, "Repo URL").value).toBe("https://mine/a");
    expect(roles(section(el, "Repo"), "alert")).toEqual([]);

    click(button(el, "Save"));
    await settle();
    expect(server.save.mock.calls[1]?.[0]).toEqual({
      value: { name: "a", url: "https://mine/a" },
      base_version: 2,
    });
    expect(server.current()).toMatchObject({ version: 3, value: { url: "https://mine/a" } });
  });

  it("shows validate-only issues after a pause, without saving", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live />);
    await settle();
    const name = textbox(el, "Repo name");

    type(name, "A");
    expect(server.validate).not.toHaveBeenCalled();
    await settle(VALIDATE_DELAY_MS + 50);

    expect(server.validate).toHaveBeenCalledTimes(1);
    expect(server.validate.mock.calls[0]?.[0]).toEqual({
      value: { name: "A", url: "https://x/a" },
    });
    expect(description(name)).toBe("name rule");
    expect(server.save).not.toHaveBeenCalled();
    expect(server.current().version).toBe(1);
  });

  it("gives a page with two documents one leave warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { el, router } = mount(
      <TwoPage first={fakeServer(VALID)} second={fakeServer({ name: "b", url: "https://x/b" })} />,
    );
    await settle();

    type(textbox(el, "First URL"), "https://y/a");
    type(textbox(el, "Second URL"), "https://y/b");

    await act(async () => {
      await router.navigate("/other");
    });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/settings");

    // A query-string change (a peek opening) keeps the page, so it doesn't ask.
    await act(async () => {
      await router.navigate("/settings?peek=AB-1");
    });
    expect(confirm).toHaveBeenCalledTimes(1);

    confirm.mockReturnValue(true);
    await act(async () => {
      await router.navigate("/other");
    });
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(router.state.location.pathname).toBe("/other");
    expect(warn).not.toHaveBeenCalled();
  });
});
