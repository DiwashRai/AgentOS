import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import {
  ADMINS_ONLY,
  isWaiting,
  lastSignIn,
  roleHint,
  ROLES,
  rowErrors,
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

  it("puts people waiting for approval first, keeping name order within each group", () => {
    const out = sortUsers([
      user(1, "Anna"),
      user(2, "zed", { role: "pending" }),
      user(3, "Bea", { role: "pending" }),
      user(4, "Cal", { role: "admin" }),
      user(5, "Abe", { role: "pending", disabled: true }),
      user(6, "Dov", { disabled: true }),
    ]);
    expect(out.map((u) => u.id)).toEqual([3, 2, 1, 4, 5, 6]);
  });

  it("does not mutate its input", () => {
    const input = [user(2, "b"), user(1, "a")];
    sortUsers(input);
    expect(input.map((u) => u.id)).toEqual([2, 1]);
  });
});

describe("isWaiting", () => {
  it("is a pending person who may still sign in", () => {
    expect(isWaiting(user(1, "a", { role: "pending" }))).toBe(true);
    expect(isWaiting(user(1, "a", { role: "pending", disabled: true }))).toBe(false);
    expect(isWaiting(user(1, "a", { role: "viewer" }))).toBe(false);
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

describe("rowErrors", () => {
  it("is empty without a failure", () => {
    expect(rowErrors(null)).toEqual([]);
    expect(rowErrors(undefined)).toEqual([]);
  });

  it("turns a 403 into the admins-only line", () => {
    expect(rowErrors(new ApiError(403, "forbidden", "x"))).toEqual([ADMINS_ONLY]);
  });

  it("shows every issue of a 422 at the row, a message-only refusal as is", () => {
    const refusal = new ApiError(422, "validation_error", "x", [{ loc: [], message: "demote" }]);
    expect(rowErrors(refusal)).toEqual(["demote"]);

    const body = new ApiError(422, "validation_error", "x", [
      { loc: ["role"], message: "bad" },
      { loc: ["disabled"], message: "bad" },
    ]);
    expect(rowErrors(body)).toEqual(["role: bad", "disabled: bad"]);
  });

  it("shows the message of a failure without issues", () => {
    expect(rowErrors(new ApiError(404, "user_not_found", "gone"))).toEqual(["gone"]);
    expect(rowErrors(new Error("offline"))).toEqual(["offline"]);
    expect(rowErrors("??")).toHaveLength(1);
  });
});

describe("summary and roleHint", () => {
  it("counts people and disabled ones", () => {
    expect(summary([user(1, "a")])).toBe("1 person");
    expect(summary([user(1, "a"), user(2, "b", { disabled: true })])).toBe("2 people · 1 disabled");
  });

  it("says how many are waiting for approval, when any are", () => {
    const pending = { role: "pending" } as const;
    expect(
      summary([
        user(1, "a", pending),
        user(2, "b", pending),
        user(3, "c", { ...pending, disabled: true }),
        user(4, "d"),
      ]),
    ).toBe("4 people · 2 waiting for approval · 1 disabled");
    expect(summary([user(1, "a", pending)])).toBe("1 person · 1 waiting for approval");
  });

  it("has a word and a hint for every role, pending first", () => {
    expect(ROLES.map((r) => r.value)).toEqual(["pending", "viewer", "writer", "admin"]);
    expect(ROLES[0]?.label).toBe("Pending");
    expect(roleHint("pending")).toMatch(/until approved/);
    expect(roleHint("viewer")).toMatch(/talks/);
    expect(roleHint("writer")).toMatch(/runs/);
    expect(roleHint("admin")).toMatch(/users/);
  });
});
