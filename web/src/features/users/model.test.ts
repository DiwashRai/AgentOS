import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import {
  describePatchError,
  lastSignIn,
  locksFor,
  roleHint,
  shortDate,
  sortUsers,
  summary,
  type User,
} from "./model";

function user(id: number, display_name: string, over: Partial<User> = {}): User {
  return {
    id,
    display_name,
    email: `${display_name.toLowerCase()}@example.com`,
    avatar_url: null,
    role: "viewer",
    disabled: false,
    env_admin: false,
    created_at: "2026-10-01T12:00:00Z",
    last_login_at: null,
    ...over,
  };
}

const NOW = Date.parse("2026-10-11T12:00:00Z");

describe("sortUsers", () => {
  it("puts disabled people last, then sorts by name ignoring case, then by id", () => {
    const out = sortUsers([
      user(1, "tom", { disabled: true }),
      user(2, "Priya"),
      user(3, "maya"),
      user(4, "Anna", { disabled: true }),
      user(6, "Maya"),
    ]);
    expect(out.map((u) => u.id)).toEqual([3, 6, 2, 4, 1]);
  });

  it("does not mutate its input", () => {
    const input = [user(2, "b"), user(1, "a")];
    sortUsers(input);
    expect(input.map((u) => u.id)).toEqual([2, 1]);
  });
});

describe("locksFor", () => {
  it("locks role and disabling on the signed-in admin's own row only", () => {
    expect(locksFor({ id: 2 }, 1)).toEqual({ role: undefined, disabled: undefined });
    const own = locksFor({ id: 1 }, 1);
    expect(own.role).toMatch(/own role/);
    expect(own.disabled).toMatch(/disable yourself/);
  });

  it("locks nothing while the viewer is unknown", () => {
    expect(locksFor({ id: 1 }, undefined)).toEqual({ role: undefined, disabled: undefined });
  });
});

describe("lastSignIn", () => {
  it("says never when there is no sign-in", () => {
    expect(lastSignIn(null, NOW)).toBe("never signed in");
  });

  it("reads as a relative age", () => {
    expect(lastSignIn("2026-10-11T11:59:40Z", NOW)).toBe("just now");
    expect(lastSignIn("2026-10-11T09:00:00Z", NOW)).toBe("3h ago");
    expect(lastSignIn("2026-09-29T12:00:00Z", NOW)).toBe("12d ago");
  });
});

describe("shortDate", () => {
  it("formats a day, short month and year", () => {
    expect(shortDate("2026-10-01T12:00:00Z")).toBe("1 Oct 2026");
  });

  it("is empty for an unparseable value", () => {
    expect(shortDate("nope")).toBe("");
  });
});

describe("describePatchError", () => {
  it("turns a 403 into the admins-only line", () => {
    expect(describePatchError(new ApiError(403, "forbidden", "Requires the admin role"))).toBe(
      "Only admins can manage users.",
    );
  });

  it("shows the server's message otherwise (e.g. an env admin can't be demoted)", () => {
    const e = new ApiError(422, "validation_error", "This user is listed in AGENTOS_ADMINS");
    expect(describePatchError(e)).toBe("This user is listed in AGENTOS_ADMINS");
    expect(describePatchError(new Error("offline"))).toBe("offline");
    expect(describePatchError("??")).toBe("Something went wrong.");
  });
});

describe("summary and roleHint", () => {
  it("counts people and disabled ones", () => {
    expect(summary([user(1, "a")])).toBe("1 person");
    expect(summary([user(1, "a"), user(2, "b", { disabled: true })])).toBe("2 people · 1 disabled");
  });

  it("has a hint for every role", () => {
    expect(roleHint("viewer")).toMatch(/talks/);
    expect(roleHint("writer")).toMatch(/runs/);
    expect(roleHint("admin")).toMatch(/users/);
  });
});
