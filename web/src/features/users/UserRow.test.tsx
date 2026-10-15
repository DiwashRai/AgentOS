// Markup checks via server rendering (node environment; no DOM library needed).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ENV_ADMIN_NOTE, type User } from "./model";
import { UserRow, type UserRowProps } from "./UserRow";

const maya: User = {
  id: 3,
  display_name: "Maya Chen",
  email: "maya@example.com",
  avatar_url: null,
  role: "writer",
  disabled: false,
  env_admin: false,
  created_at: "2026-10-01T12:00:00Z",
  last_login_at: null,
};

function render(over: Partial<UserRowProps> = {}) {
  const props: UserRowProps = {
    user: maya,
    self: false,
    now: Date.parse("2026-10-11T12:00:00Z"),
    pending: false,
    errors: [],
    onPatch: () => undefined,
    ...over,
  };
  return renderToStaticMarkup(
    <table>
      <UserRow {...props} />
    </table>,
  );
}

const selectTag = (html: string) => /<select[^>]*>/.exec(html)?.[0] ?? "";

describe("UserRow", () => {
  it("shows the person, their role with its hint, status and times", () => {
    const html = render();
    expect(html).toContain("Maya Chen");
    expect(html).toContain("maya@example.com");
    expect(html).toContain('aria-label="Role for Maya Chen"');
    expect(html).toMatch(/<option value="writer" selected="">Writer<\/option>/);
    expect(html).toContain("Also tickets, pipeline, files and runs");
    expect(html).toContain("Active");
    expect(html).toContain("never signed in");
    expect(html).toContain("1 Oct 2026");
    expect(selectTag(html)).not.toContain("disabled");
  });

  it("keeps role and Disable usable on your own row (the server refuses, not the row)", () => {
    const html = render({ self: true });
    expect(html).toContain(">You<");
    expect(selectTag(html)).not.toContain("disabled");
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>Disable<\/button>/);
  });

  it("offers Enable for a disabled person and marks the row", () => {
    const html = render({ user: { ...maya, disabled: true } });
    expect(html).toContain("data-disabled");
    expect(html).toContain("Disabled");
    expect(html).toMatch(/<button[^>]*aria-label="Enable Maya Chen"[^>]*>Enable<\/button>/);
  });

  it("shows the role being set and disables controls while pending", () => {
    const html = render({ pending: true, pendingRole: "admin" });
    expect(html).toContain('aria-busy="true"');
    expect(html).toMatch(/<option value="admin" selected="">Admin<\/option>/);
    expect(selectTag(html)).toContain("disabled");
  });

  it("offers Approve and says why on a pending row, with Pending in the picker", () => {
    const html = render({ user: { ...maya, role: "pending" } });
    expect(html).toContain("data-waiting");
    expect(html).toContain("Waiting for approval");
    expect(html).not.toContain(">Active<");
    expect(html).toMatch(/<option value="pending" selected="">Pending<\/option>/);
    expect(html).toContain("Sees nothing until approved");
    expect(html).toMatch(/<button[^>]*aria-label="Approve Maya Chen"[^>]*>Approve<\/button>/);
    expect(selectTag(html)).not.toContain("disabled");
    expect(html).toContain(">Disable<");
  });

  it("has no Approve for an approved person or a disabled pending one", () => {
    expect(render()).not.toContain(">Approve<");
    const off = render({ user: { ...maya, role: "pending", disabled: true } });
    expect(off).not.toContain(">Approve<");
    expect(off).not.toContain("data-waiting");
    expect(off).toContain("Disabled");
  });

  it("disables Approve while a change is in flight", () => {
    const html = render({
      user: { ...maya, role: "pending" },
      pending: true,
      pendingRole: "writer",
    });
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Approve<\/button>/);
  });

  it("says a config admin is set in server config, in place of the hint, with controls usable", () => {
    const html = render({ user: { ...maya, role: "admin", env_admin: true } });
    expect(html).toContain(`>${ENV_ADMIN_NOTE}<`);
    expect(html).not.toContain("Also users and the template");
    expect(selectTag(html)).not.toContain("disabled");
  });

  it("shows a failed change's problems inline, and nothing without one", () => {
    expect(render()).not.toContain('role="alert"');
    const html = render({ errors: ["demote"] });
    expect(html).toMatch(/role="alert"[^>]*>demote</);
  });
});
