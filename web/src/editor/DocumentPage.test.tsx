// @vitest-environment jsdom
// A versioned document page: its loading / admins-only / error states, a save sending the value
// over its base version, a 422 putting every issue on its field at once, a 409 whose Reload keeps
// the draft and saves over the fresh version (and whose Discard shows the other admin's copy),
// validate-only issues after a pause, a save answering late, a draft reverted to the saved copy
// starting over, and one leave warning for the page's documents and its own `dirty`. Real TanStack
// Query and react-router; a fake server.
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
import { useDraftState } from "./useDraft";

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
  const apply = (body: DocumentSave<Repo>): Promise<Read> => {
    if (body.base_version !== doc.version) {
      return Promise.reject(new ApiError(409, "stale_version", "stale"));
    }
    const issues = check(body.value);
    if (issues.length > 0) return Promise.reject(invalid(issues));
    doc = { ...doc, value: body.value, version: doc.version + 1 };
    return Promise.resolve(doc);
  };
  const save = vi.fn(apply);
  return {
    current: () => doc,
    overwrite(next: Repo) {
      doc = { ...doc, value: next, version: doc.version + 1 };
    },
    /** Hold the next save until the returned function is called; it then answers as usual. */
    hold(): () => void {
      let release: () => void = () => undefined;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      save.mockImplementationOnce((body) => gate.then(() => apply(body)));
      return () => {
        release();
      };
    },
    read: vi.fn(() => Promise.resolve(doc)),
    save,
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

/** `viewerRole` undefined: the signed-in person is still loading. */
function OnePage({
  server,
  viewerRole,
  live,
}: {
  server: Server;
  viewerRole: string | undefined;
  live: boolean;
}) {
  const doc = useRepoDocument(server, "one", live);
  return (
    <DocumentPage
      viewer={viewerRole === undefined ? undefined : { role: viewerRole }}
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

/** A document plus a plain draft on the same page (the runtimes on Runners), passed as `dirty`. */
function DraftPropPage({ server }: { server: Server }) {
  const doc = useRepoDocument(server, "one", false);
  const runtime = useDraftState("claude", 1, (a, b) => a === b);
  return (
    <DocumentPage
      viewer={{ role: "admin" }}
      adminsOnly={ADMINS_ONLY}
      noun="runner"
      sources={[doc]}
      dirty={runtime.dirty}
    >
      {() => (
        <>
          <RepoSection title="Repo" doc={doc} />
          <Field label="Runtime">
            {({ id }) => (
              <Input
                id={id}
                value={runtime.value ?? ""}
                onChange={(e) => {
                  runtime.edit(e.target.value);
                }}
              />
            )}
          </Field>
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
  return { el, router, client };
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

async function navigate(router: ReturnType<typeof createMemoryRouter>, to: string) {
  await act(async () => {
    await router.navigate(to);
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

  it("shows loading while the viewer is unknown, even once the document has loaded", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole={undefined} live={false} />);
    await settle();

    expect(server.read).toHaveBeenCalled();
    expect(roles(el, "status")).toHaveLength(1);
    expect(roles(el, "status")).not.toEqual([ADMINS_ONLY]);
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

  it("keeps a newer validate's issues when an older save's 422 comes back late", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live />);
    await settle();
    const name = textbox(el, "Repo name");
    const url = textbox(el, "Repo URL");
    const release = server.hold();

    // The save sends a bad name; while it is out, the name is fixed and the URL broken.
    type(name, "A");
    click(button(el, "Save"));
    type(name, "a");
    type(url, "ftp://x/a");
    await settle(VALIDATE_DELAY_MS + 50);
    expect(description(url)).toBe("url rule");

    release();
    await settle();

    expect(server.save).toHaveBeenCalledTimes(1);
    expect(description(url)).toBe("url rule");
    expect(name.getAttribute("aria-invalid")).toBe("false");
    expect(button(el, "Save").disabled).toBe(true);
  });

  it("keeps an edit made while a save is in flight, over the version that save wrote", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live />);
    await settle();
    const url = textbox(el, "Repo URL");
    const release = server.hold();

    type(url, "https://y/a");
    click(button(el, "Save"));
    type(url, "ftp://y/a");
    await settle(VALIDATE_DELAY_MS + 50);
    release();
    await settle();

    expect(server.current()).toMatchObject({ version: 2, value: { url: "https://y/a" } });
    expect(url.value).toBe("ftp://y/a");
    expect(description(url)).toBe("url rule");

    type(url, "https://z/a");
    click(button(el, "Save"));
    await settle();
    expect(server.save.mock.calls[1]?.[0].base_version).toBe(2);
    expect(server.current()).toMatchObject({ version: 3, value: { url: "https://z/a" } });
  });

  it("discards after a conflict and shows the other admin's copy", async () => {
    const server = fakeServer(VALID);
    const { el } = mount(<OnePage server={server} viewerRole="admin" live={false} />);
    await settle();

    server.overwrite({ name: "a", url: "https://other/a" });
    type(textbox(el, "Repo URL"), "https://mine/a");
    click(button(el, "Save"));
    await settle();
    expect(roles(section(el, "Repo"), "alert")).toHaveLength(1);

    click(button(section(el, "Repo"), "Discard"));
    await settle();

    expect(textbox(el, "Repo URL").value).toBe("https://other/a");
    expect(roles(section(el, "Repo"), "alert")).toEqual([]);
    expect(button(el, "Save").disabled).toBe(true);
  });

  it("starts a draft reverted to the saved copy over the current version", async () => {
    const server = fakeServer(VALID);
    const { el, client } = mount(<OnePage server={server} viewerRole="admin" live={false} />);
    await settle();

    type(textbox(el, "Repo URL"), "https://y/a");
    type(textbox(el, "Repo URL"), "https://x/a");
    expect(button(el, "Save").disabled).toBe(true);

    // Another admin's save arrives with the next read.
    server.overwrite({ name: "a", url: "https://other/a" });
    void client.refetchQueries();
    await settle();
    expect(textbox(el, "Repo URL").value).toBe("https://other/a");

    type(textbox(el, "Repo URL"), "https://mine/a");
    click(button(el, "Save"));
    await settle();

    expect(server.save.mock.calls[0]?.[0].base_version).toBe(2);
    expect(server.current()).toMatchObject({ version: 3, value: { url: "https://mine/a" } });
  });

  it("gives a page with two documents one leave warning, over either document", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { el, router } = mount(
      <TwoPage first={fakeServer(VALID)} second={fakeServer({ name: "b", url: "https://x/b" })} />,
    );
    await settle();

    type(textbox(el, "Second URL"), "https://y/b");

    await navigate(router, "/other");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/settings");

    // A query-string change (a peek opening) keeps the page, so it doesn't ask.
    await navigate(router, "/settings?peek=AB-1");
    expect(confirm).toHaveBeenCalledTimes(1);

    confirm.mockReturnValue(true);
    await navigate(router, "/other");
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(router.state.location.pathname).toBe("/other");
    expect(warn).not.toHaveBeenCalled();
  });

  it("warns for a plain draft passed as the page's dirty", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { el, router } = mount(<DraftPropPage server={fakeServer(VALID)} />);
    await settle();

    type(textbox(el, "Runtime"), "copilot");
    await navigate(router, "/other");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/settings");

    // Back to the saved copy: nothing to lose, so it leaves without asking.
    type(textbox(el, "Runtime"), "claude");
    await navigate(router, "/other");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/other");
    expect(warn).not.toHaveBeenCalled();
  });
});
