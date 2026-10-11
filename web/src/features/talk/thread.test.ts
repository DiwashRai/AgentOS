import { describe, expect, it } from "vitest";

import type { Schemas } from "../../api/client";
import {
  clockTime,
  dayLabel,
  fullTime,
  groupThread,
  placeholder,
  speakers,
  type BlockItem,
} from "./thread";

type Person = Schemas["Person"];

const ME: Person = { id: 1, display_name: "Diwash Rai", avatar_url: null };
const MAYA: Person = { id: 2, display_name: "Maya Lund", avatar_url: null };
const TOM: Person = { id: 3, display_name: "Tom", avatar_url: null };
const PRIYA: Person = { id: 4, display_name: "Priya Nair", avatar_url: null };
const SAM: Person = { id: 5, display_name: "Sam Ortiz", avatar_url: null };

// Local times, so day boundaries hold in any test timezone.
const NOW = new Date(2026, 9, 11, 18, 0);
let nextId = 1;
function msg(author: Person, at: Date, body = "hi"): Schemas["ChatMessage"] {
  return { id: nextId++, ticket_key: "AOS-1", author, body, created_at: at.toISOString() };
}
const at = (day: number, h: number, m: number, s = 0) => new Date(2026, 9, day, h, m, s);

function blocks(items: ReturnType<typeof groupThread>): BlockItem[] {
  return items.filter((i): i is BlockItem => i.kind === "block");
}

describe("groupThread", () => {
  it("groups consecutive messages by one author within 5 minutes", () => {
    const items = groupThread(
      [msg(MAYA, at(11, 10, 0)), msg(MAYA, at(11, 10, 3)), msg(MAYA, at(11, 10, 8))],
      ME.id,
      NOW,
    );
    expect(items.map((i) => i.kind)).toEqual(["day", "block"]);
    expect(blocks(items)[0]?.messages).toHaveLength(3);
  });

  it("measures the 5 minutes from the previous message, inclusive", () => {
    const items = groupThread(
      [msg(MAYA, at(11, 10, 0)), msg(MAYA, at(11, 10, 5)), msg(MAYA, at(11, 10, 10, 1))],
      ME.id,
      NOW,
    );
    expect(blocks(items).map((b) => b.messages.length)).toEqual([2, 1]);
  });

  it("starts a new block when the author changes", () => {
    const items = groupThread(
      [msg(MAYA, at(11, 10, 0)), msg(TOM, at(11, 10, 1)), msg(MAYA, at(11, 10, 2))],
      ME.id,
      NOW,
    );
    expect(blocks(items).map((b) => b.author.id)).toEqual([MAYA.id, TOM.id, MAYA.id]);
  });

  it("puts a separator at each day boundary and never groups across it", () => {
    const items = groupThread(
      [msg(MAYA, at(9, 9, 0)), msg(MAYA, at(10, 23, 58)), msg(MAYA, at(11, 0, 1))],
      ME.id,
      NOW,
    );
    expect(items.map((i) => (i.kind === "day" ? i.label : i.messages.length))).toEqual([
      "Fri 9 Oct",
      1,
      "Yesterday",
      1,
      "Today",
      1,
    ]);
  });

  it("marks my blocks and keys blocks by their first message", () => {
    const first = msg(ME, at(11, 10, 0));
    const items = groupThread([first, msg(ME, at(11, 10, 1))], ME.id, NOW);
    const b = blocks(items)[0];
    expect(b?.mine).toBe(true);
    expect(b?.key).toBe(`m${String(first.id)}`);
    expect(b?.at).toBe(first.created_at);
    expect(blocks(groupThread([first], null, NOW))[0]?.mine).toBe(false);
  });

  it("returns nothing for an empty thread", () => {
    expect(groupThread([], ME.id, NOW)).toEqual([]);
  });
});

describe("times", () => {
  it("labels days", () => {
    expect(dayLabel(at(11, 0, 0), NOW)).toBe("Today");
    expect(dayLabel(at(10, 12, 0), NOW)).toBe("Yesterday");
    expect(dayLabel(at(8, 12, 0), NOW)).toBe("Thu 8 Oct");
    expect(dayLabel(new Date(2025, 11, 31, 9, 0), NOW)).toBe("Wed 31 Dec 2025");
  });

  it("formats clock and full times", () => {
    expect(clockTime(at(11, 9, 5))).toBe("09:05");
    expect(fullTime(at(11, 10, 15))).toBe("Sun 11 Oct 2026, 10:15");
  });
});

describe("speakers and placeholder", () => {
  it("lists the other people once, in order of first message", () => {
    const list = [msg(MAYA, at(11, 9, 0)), msg(ME, at(11, 9, 1)), msg(TOM, at(11, 9, 2))];
    list.push(msg(MAYA, at(11, 9, 3)));
    expect(speakers(list, ME.id).map((p) => p.id)).toEqual([MAYA.id, TOM.id]);
  });

  it("names up to three people by first name", () => {
    expect(placeholder([])).toBe("Say something to the team");
    expect(placeholder([PRIYA])).toBe("Say something to Priya");
    expect(placeholder([MAYA, TOM])).toBe("Say something to Maya and Tom");
    expect(placeholder([MAYA, TOM, PRIYA])).toBe("Say something to Maya, Tom and Priya");
    expect(placeholder([MAYA, TOM, PRIYA, SAM])).toBe("Say something to Maya, Tom and Priya");
  });
});
