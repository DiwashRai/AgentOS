// Markup checks via server rendering (node environment; no DOM library needed).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { locksFor, type User } from "./model";
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
    locks: locksFor(maya, 1),
    now: Date.parse("2026-10-11T12:00:00Z"),
    pending: false,
    error: null,
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

  it("locks role and Disable on your own row, saying why", () => {
    const html = render({ self: true, locks: locksFor(maya, 3) });
    expect(html).toContain(">You<");
    expect(selectTag(html)).toContain("disabled");
    expect(html).toContain('title="You can&#x27;t change your own role. Another admin can."');
    expect(html).toContain('title="You can&#x27;t disable yourself. Another admin can."');
  });

  it("offers Enable for a disabled person and marks the row", () => {
    const html = render({ user: { ...maya, disabled: true } });
    expect(html).toContain("data-disabled");
    expect(html).toContain("Disabled");
    expect(html).toContain(">Enable<");
  });

  it("shows the role being set and disables controls while pending", () => {
    const html = render({ pending: true, pendingRole: "admin" });
    expect(html).toContain('aria-busy="true"');
    expect(html).toMatch(/<option value="admin" selected="">Admin<\/option>/);
    expect(selectTag(html)).toContain("disabled");
  });

  it("shows a failed change inline", () => {
    const html = render({ error: "You can't demote yourself" });
    expect(html).toContain('role="alert"');
    expect(html).toContain("You can&#x27;t demote yourself");
  });
});
