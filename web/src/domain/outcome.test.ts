import { describe, expect, it } from "vitest";

import { person, ticket } from "./fixtures";
import { outcomeOf, resolvedBy } from "./outcome";

const resolution = (outcome: "completed" | "abandoned", note: string | null = null) => ({
  outcome,
  note,
  by: person(4, "Diwash Rai"),
  at: "2026-10-11T10:00:00Z",
});

describe("outcomeOf", () => {
  it("reads resolution.outcome on a done ticket", () => {
    expect(
      outcomeOf(
        ticket({ attention: "done", state: "closed", resolution: resolution("abandoned") }),
      ),
    ).toBe("abandoned");
    expect(
      outcomeOf(ticket({ attention: "done", state: "done", resolution: resolution("completed") })),
    ).toBe("completed");
  });

  it("falls back to state without a resolution: closed is abandoned, done is complete", () => {
    expect(outcomeOf(ticket({ attention: "done", state: "closed" }))).toBe("abandoned");
    expect(outcomeOf(ticket({ attention: "done", state: "done" }))).toBe("completed");
  });

  it("only done tickets have an outcome", () => {
    expect(outcomeOf(ticket({ attention: "blocked", state: "closed" }))).toBeNull();
    expect(outcomeOf(ticket({ attention: "inbox" }))).toBeNull();
  });
});

describe("resolvedBy", () => {
  it("names who closed it and how long ago", () => {
    const r = resolution("abandoned");
    expect(resolvedBy(r, Date.parse("2026-10-11T12:00:00Z"))).toBe("Diwash · 2h ago");
    expect(resolvedBy(r, Date.parse("2026-10-11T10:00:20Z"))).toBe("Diwash · just now");
  });
});
