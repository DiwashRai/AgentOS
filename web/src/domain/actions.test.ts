import { describe, expect, it } from "vitest";

import { type ActionSet, ticketActions } from "./actions";
import { gate, person, run, step, ticket } from "./fixtures";

/** The group as `id:variant` left → right, the primary last (how it renders). */
const shape = (s: ActionSet) =>
  [...s.secondary, ...(s.primary ? [s.primary] : [])].map((a) => `${a.id}:${a.variant}`);

const parkedOnYou = ticket({
  attention: "needs_you",
  rail: [step("Plan", "done", { gate: gate("you") }), step("Build", "pending")],
});

describe("ticketActions", () => {
  describe("needs you", () => {
    it("parked at a gate with a stage after it: Approve, with Request changes", () => {
      const row = ticketActions(parkedOnYou, "row");
      expect(shape(row)).toEqual(["request_changes:quiet", "approve:you"]);
      expect(row.primary?.label).toBe("Approve");
      expect(row.primary?.title).toBe("Plan sign-off");
    });

    it("the header adds a quiet Abandon but no Dispatch run (it would just do what Approve does)", () => {
      expect(shape(ticketActions(parkedOnYou, "header"))).toEqual([
        "abandon:quiet",
        "request_changes:quiet",
        "approve:you",
      ]);
    });

    it("never offers Complete while a stage follows the gate", () => {
      for (const ctx of ["row", "header"] as const) {
        const s = ticketActions(parkedOnYou, ctx);
        expect([s.primary, ...s.secondary].some((a) => a?.id === "complete")).toBe(false);
      }
    });

    it("skipped stages after the gate don't count: Complete", () => {
      const t = ticket({
        attention: "needs_you",
        rail: [step("Plan", "done", { gate: gate("you") }), step("Build", "skipped")],
      });
      const s = ticketActions(t, "row");
      expect(s.primary).toMatchObject({ id: "complete", label: "Complete", variant: "you" });
      expect(shape(s)).toEqual(["abandon:quiet", "request_changes:quiet", "complete:you"]);
    });

    it("an upcoming (stale) stage after the gate still counts", () => {
      const t = ticket({
        attention: "needs_you",
        rail: [step("Plan", "done", { gate: gate("you") }), step("Build", "stale")],
      });
      expect(ticketActions(t, "row").primary?.id).toBe("approve");
    });

    it("the last gate: Complete", () => {
      const t = ticket({
        attention: "needs_you",
        rail: [step("Plan", "done"), step("Build", "done", { gate: gate("you") })],
      });
      expect(ticketActions(t, "header").primary?.id).toBe("complete");
    });

    it("no parked gate (a succeeded run): Complete and Abandon, no Request changes", () => {
      const t = ticket({
        attention: "needs_you",
        latest_run: run("succeeded"),
        rail: [step("Plan", "done"), step("Build", "done")],
      });
      expect(shape(ticketActions(t, "row"))).toEqual(["abandon:quiet", "complete:you"]);
      expect(shape(ticketActions(t, "header"))).toEqual(["abandon:quiet", "complete:you"]);
    });

    it("reads only a gate on you, not passed or pending ones", () => {
      const t = ticket({
        attention: "needs_you",
        rail: [step("Plan", "done", { gate: gate("passed") }), step("Build", "pending")],
      });
      expect(ticketActions(t, "row").primary?.id).toBe("complete");
    });
  });

  describe("waiting", () => {
    const waiting = ticket({
      attention: "waiting",
      rail: [
        step("Plan", "done", {
          gate: gate("waiting", { label: "Security review", person: person(2, "Sam Lee") }),
        }),
        step("Build", "pending"),
      ],
    });

    it("has no ink primary; a row just opens the ticket", () => {
      const s = ticketActions(waiting, "row");
      expect(s.primary).toBeNull();
      expect(shape(s)).toEqual(["open:default"]);
    });

    it("the header offers a plain Dispatch run that says who it waits on", () => {
      const s = ticketActions(waiting, "header");
      expect(shape(s)).toEqual(["dispatch:default"]);
      expect(s.secondary[0]?.title).toBe("Waiting on Sam at Security review");
    });

    it("falls back to now.person, then 'someone'", () => {
      const viaNow = ticket({
        attention: "waiting",
        now: { actor: null, person: person(3, "Ana Ruiz"), progress: null, run_id: null, text: "" },
      });
      expect(ticketActions(viaNow, "header").secondary[0]?.title).toBe("Waiting on Ana");
      const nobody = ticket({ attention: "waiting" });
      expect(ticketActions(nobody, "header").secondary[0]?.title).toBe("Waiting on someone");
    });
  });

  describe("blocked", () => {
    const blocked = ticket({ attention: "blocked", latest_run: run("failed") });

    it("Retry is the ink primary; a row can also abandon or open the ticket", () => {
      const s = ticketActions(blocked, "row");
      expect(s.primary).toMatchObject({ id: "retry", label: "Retry", variant: "ink" });
      expect(shape(s)).toEqual(["abandon:quiet", "open:default", "retry:ink"]);
    });

    it("the header has Retry with a quiet Abandon", () => {
      expect(shape(ticketActions(blocked, "header"))).toEqual(["abandon:quiet", "retry:ink"]);
    });
  });

  describe("running and queued", () => {
    it.each(["running", "queued"] as const)("%s: Cancel, no primary", (attention) => {
      const s = ticketActions(ticket({ attention, latest_run: run("running") }), "header");
      expect(s.primary).toBeNull();
      expect(s.cancelling).toBe(false);
      expect(s.secondary).toEqual([{ id: "cancel", label: "Cancel", variant: "default" }]);
    });

    it("shows Cancelling… (disabled) while a cancel is requested", () => {
      const t = ticket({
        attention: "running",
        latest_run: run("running", { cancel_requested_at: "2026-10-11T10:05:00Z" }),
      });
      const s = ticketActions(t, "row");
      expect(s.cancelling).toBe(true);
      expect(s.secondary[0]).toMatchObject({ id: "cancel", label: "Cancelling…", disabled: true });
    });
  });

  describe("inbox", () => {
    it("the header's primary is the accent Dispatch run", () => {
      const s = ticketActions(ticket({ attention: "inbox" }), "header");
      expect(s.primary).toMatchObject({ id: "dispatch", label: "Dispatch run", variant: "accent" });
      expect(shape(s)).toEqual(["abandon:quiet", "dispatch:accent"]);
    });

    it("a row never carries the accent or Abandon: it opens the ticket", () => {
      const s = ticketActions(ticket({ attention: "inbox" }), "row");
      expect(shape(s)).toEqual(["open:ink"]);
    });
  });

  describe("dispatch blocked (the server's dispatch_block)", () => {
    const block = { code: "base_branch_missing" as const, text: "Set a base branch first" };

    it("disables the header's Dispatch run, titled with the server's text", () => {
      const s = ticketActions(ticket({ attention: "inbox", dispatch_block: block }), "header");
      expect(s.primary).toMatchObject({
        id: "dispatch",
        variant: "accent",
        disabled: true,
        title: "Set a base branch first",
      });
      expect(s.secondary[0]).toMatchObject({ id: "abandon" });
      expect(s.secondary[0]?.disabled).toBeUndefined();
    });

    it("disables Retry and Approve (both POST runs), and a waiting ticket's Dispatch run", () => {
      const blocked = ticket({ attention: "blocked", dispatch_block: block });
      expect(ticketActions(blocked, "row").primary).toMatchObject({
        id: "retry",
        disabled: true,
        title: block.text,
      });
      const approve = ticketActions({ ...parkedOnYou, dispatch_block: block }, "row").primary;
      expect(approve).toMatchObject({ id: "approve", disabled: true, title: block.text });
      const waiting = ticketActions(
        ticket({ attention: "waiting", dispatch_block: block }),
        "header",
      );
      expect(waiting.secondary[0]).toMatchObject({ id: "dispatch", disabled: true });
    });

    it("leaves the actions that don't dispatch alone", () => {
      const last = ticket({
        attention: "needs_you",
        dispatch_block: block,
        rail: [step("Plan", "done"), step("Build", "done", { gate: gate("you") })],
      });
      const s = ticketActions(last, "header");
      expect(s.primary?.id).toBe("complete");
      expect([s.primary, ...s.secondary].some((a) => a?.disabled)).toBe(false);
      const row = ticketActions(ticket({ attention: "inbox", dispatch_block: block }), "row");
      expect(row.primary).toEqual({ id: "open", label: "Open", variant: "ink" });
    });

    it("changes nothing without a block", () => {
      const s = ticketActions(ticket({ attention: "inbox" }), "header");
      expect(s.primary?.disabled).toBeUndefined();
      expect(s.primary?.title).toBeUndefined();
    });
  });

  it("waiting and running tickets offer no Complete or Abandon", () => {
    for (const attention of ["waiting", "running", "queued"] as const) {
      for (const ctx of ["row", "header"] as const) {
        const s = ticketActions(ticket({ attention, latest_run: run("running") }), ctx);
        const ids = [s.primary, ...s.secondary].map((a) => a?.id);
        expect(ids).not.toContain("complete");
        expect(ids).not.toContain("abandon");
      }
    }
  });

  it("done: Reopen only", () => {
    for (const ctx of ["row", "header"] as const) {
      const s = ticketActions(ticket({ attention: "done" }), ctx);
      expect(shape(s)).toEqual(["reopen:default"]);
    }
  });

  it("puts the accent only on Dispatch run (header), you only on Approve / Complete, one strong button", () => {
    const tickets = [
      parkedOnYou,
      ticket({ attention: "needs_you" }),
      ticket({ attention: "waiting" }),
      ticket({ attention: "blocked" }),
      ticket({ attention: "running" }),
      ticket({ attention: "queued" }),
      ticket({ attention: "inbox" }),
      ticket({ attention: "done" }),
    ];
    for (const t of tickets) {
      for (const ctx of ["row", "header"] as const) {
        const all = [...ticketActions(t, ctx).secondary];
        const primary = ticketActions(t, ctx).primary;
        if (primary) all.push(primary);
        for (const a of all.filter((x) => x.variant === "accent")) {
          expect([a.id, ctx]).toEqual(["dispatch", "header"]);
        }
        for (const a of all.filter((x) => x.variant === "you")) {
          expect(["approve", "complete"]).toContain(a.id);
        }
        const strong = (x: { variant: string }) =>
          x.variant === "ink" || x.variant === "you" || x.variant === "accent";
        expect(all.filter(strong).length).toBeLessThanOrEqual(1);
        expect(ticketActions(t, ctx).secondary.some(strong)).toBe(false);
      }
    }
  });
});
