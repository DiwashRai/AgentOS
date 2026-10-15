// Markup checks via server rendering (node environment; no DOM library needed).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Age } from "./Age";
import { Avatar, AvatarSlot } from "./Avatar";
import { Button } from "./Button";
import type { StepperModel } from "./glyph";
import { PriorityIcon } from "./PriorityIcon";
import { RunMeter } from "./RunMeter";
import { StageGlyph } from "./StageGlyph";
import { StatusMark } from "./StatusMark";
import { Stepper } from "./Stepper";
import { Tabs } from "./Tabs";
import { TypeIcon } from "./TypeIcon";

const model: StepperModel = {
  live: false,
  label: "Sec review",
  stages: [
    { name: "Inbox", state: "done" },
    {
      name: "Plan",
      state: "done",
      gate: { label: "Plan sign-off", short: "Sign-off", state: "passed" },
    },
    {
      name: "Build",
      state: "done",
      gate: { label: "Build check", short: "Check", state: "passed" },
    },
    {
      name: "Review",
      state: "done",
      gate: { label: "Security review", short: "Sec review", state: "waiting", person: "Sam" },
    },
    { name: "Done", state: "upcoming" },
  ],
};

const count = (html: string, re: RegExp) => html.match(re)?.length ?? 0;

describe("StatusMark", () => {
  it("always renders the YOU pill for kind you, ignoring name; the ask follows it directly", () => {
    const html = renderToStaticMarkup(<StatusMark kind="you" name="ignored" ask="Plan sign-off" />);
    expect(html).toContain(">YOU</b>");
    expect(html).not.toContain("ignored");
    expect(html).toMatch(/>Plan sign-off<\/span>/);
    expect(html).not.toContain("·");
  });
  it("puts a dot between a name and its ask", () => {
    const html = renderToStaticMarkup(<StatusMark kind="wait" name="Sam" ask="Security review" />);
    expect(html).toContain("· Security review");
  });
  it("draws done as the tick badge", () => {
    const html = renderToStaticMarkup(<StatusMark kind="done" glyphOnly />);
    expect(html).toContain("<circle");
    expect(html).toContain('aria-label="Done"');
  });
  it("names a glyph-only mark for assistive tech", () => {
    const html = renderToStaticMarkup(<StatusMark kind="blk" glyphOnly />);
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="Blocked"');
  });
  it("has an abandoned kind, named Abandoned", () => {
    const glyph = renderToStaticMarkup(<StatusMark kind="abandoned" glyphOnly />);
    expect(glyph).toContain('aria-label="Abandoned"');
    const named = renderToStaticMarkup(<StatusMark kind="abandoned" name="Abandoned" />);
    expect(named).toContain("<b>Abandoned</b>");
  });
});

describe("StageGlyph", () => {
  it("draws one segment per stage, one diamond per gate, and the label", () => {
    const html = renderToStaticMarkup(<StageGlyph model={model} />);
    expect(count(html, /<i[ >]/g)).toBe(5);
    expect(count(html, /<u[ >]/g)).toBe(3);
    expect(html).toContain("<b>Sec review</b>");
    expect(html).toContain('title="Inbox → Plan → ◆ Plan sign-off → Build → ◆ Build check');
  });
  it("hides the label on request", () => {
    expect(renderToStaticMarkup(<StageGlyph model={model} label={false} />)).not.toContain("<b>");
  });
});

describe("StageGlyph (refresh)", () => {
  const seven: StepperModel = {
    live: false,
    label: "Sign-off",
    stages: ["Inbox", "Plan", "Implement", "Lint", "Review", "Open PR", "Done"].map((name, i) => ({
      name,
      state: i < 2 ? "done" : i === 3 ? "skipped" : "upcoming",
      gate:
        i === 1 ? { label: "Plan sign-off", short: "Sign-off", state: "you" as const } : undefined,
    })),
  };
  it("draws a 7-stage pipeline at sm in the 81px slot", () => {
    const html = renderToStaticMarkup(<StageGlyph model={seven} />);
    expect(html).not.toMatch(/class="[^"]*\bxs\b/);
    expect(html).toContain("Plan → ◆ Plan sign-off → Implement → Lint (skipped)");
  });
  it("finished: the label gets the tick", () => {
    const done: StepperModel = {
      ...seven,
      label: "Done",
      stages: seven.stages.map((st, i) => ({
        ...st,
        state: i === 6 ? "current" : st.state === "skipped" ? "skipped" : "done",
        gate: st.gate ? { ...st.gate, state: "passed" as const } : undefined,
      })),
    };
    const html = renderToStaticMarkup(<StageGlyph model={done} />);
    expect(html).toMatch(/<b><span[^>]*aria-hidden="true"><svg/);
  });
});

describe("Stepper (refresh)", () => {
  it("captions a gate on you 'Your move' (no YOU pill) and a finished last stage 'Complete'", () => {
    const parked: StepperModel = {
      live: false,
      label: "Sign-off",
      stages: [
        { name: "Inbox", state: "done" },
        {
          name: "Plan",
          state: "done",
          gate: { label: "Plan sign-off", short: "Sign-off", state: "you" },
        },
        { name: "Build", state: "upcoming" },
        { name: "Done", state: "upcoming" },
      ],
    };
    const html = renderToStaticMarkup(<Stepper model={parked} />);
    expect(html).toContain(">Your move<");
    expect(html).not.toContain(">YOU<");
    const finished: StepperModel = {
      live: false,
      label: "Done",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Build", state: "done" },
        { name: "Done", state: "current" },
      ],
    };
    const done = renderToStaticMarkup(<Stepper model={finished} />);
    expect(done).toContain(">Complete<");
    expect(done).toContain("<circle");
  });
});

describe("Stepper", () => {
  it("collapses inactive gates to bare diamonds with 3 gates", () => {
    const html = renderToStaticMarkup(<Stepper model={model} />);
    expect(html).not.toContain(">Sign-off<");
    expect(html).not.toContain(">Check<");
    expect(html).toContain(">Sec review<");
    expect(html).toContain(">Sam<");
    expect(html).toContain('title="Plan sign-off · passed"');
  });
});

describe("RunMeter", () => {
  it("is an image labelled k/n steps", () => {
    const html = renderToStaticMarkup(<RunMeter k={3} n={5} state="running" size="tile" />);
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="3/5 steps"');
    expect(count(html, /<i /g)).toBe(5);
  });
  it("sets the row width", () => {
    const html = renderToStaticMarkup(
      <RunMeter k={1} n={4} state="queued" size="row" width={36} />,
    );
    expect(html).toContain("--rm-w:36px");
    expect(html).toContain('aria-label="0/4 steps"');
  });
  it("names strip steps up to the cap", () => {
    const labels = ["Read", "Edit", "Test", "Lint", "Ship"];
    const html = renderToStaticMarkup(
      <RunMeter k={2} n={5} state="running" size="strip" labels={labels} />,
    );
    expect(count(html, /<li/g)).toBe(5);
    expect(html).toContain(">Now<");
    expect(html).toContain('title="Ship"');
  });
  it("draws bare strip segments past the cap", () => {
    const labels = Array.from({ length: 30 }, (_, i) => `Step text ${String(i)}`);
    const html = renderToStaticMarkup(
      <RunMeter k={4} n={30} state="running" size="strip" labels={labels} />,
    );
    expect(count(html, /<li/g)).toBe(0);
    expect(count(html, /<i /g)).toBe(30);
    expect(html).not.toContain("Step text");
    expect(html).not.toContain(">Now<");
    expect(html).toContain("--rm-n:30");
    expect(html).toContain('aria-label="4/30 steps"');
  });
});

describe("Button", () => {
  it("defaults to type=button and gives accent the arrow", () => {
    const html = renderToStaticMarkup(<Button variant="accent">Dispatch run</Button>);
    expect(html).toContain('type="button"');
    expect(html).toContain("<svg");
  });
  it("gives the you variant no arrow, and marks a litOnRow button", () => {
    const you = renderToStaticMarkup(<Button variant="you">Approve</Button>);
    expect(you).not.toContain("<svg");
    const plain = renderToStaticMarkup(<Button variant="outline">Take in</Button>);
    const lit = renderToStaticMarkup(
      <Button variant="outline" litOnRow>
        Take in
      </Button>,
    );
    expect(lit).not.toEqual(plain);
  });
});

describe("Avatar", () => {
  it("shows initials and names the person", () => {
    const html = renderToStaticMarkup(<Avatar name="Diwash Rai" id={1} />);
    expect(html).toContain(">DR</span>");
    expect(html).toContain('title="Diwash Rai"');
  });
  it("uses the identity hue by default and the talk faces at 26px", () => {
    const row = renderToStaticMarkup(<Avatar name="Diwash Rai" id={1} />);
    const talk = renderToStaticMarkup(<Avatar name="Diwash Rai" id={1} size={26} />);
    const forced = renderToStaticMarkup(<Avatar name="Diwash Rai" id={1} palette="talk" />);
    expect(row).not.toEqual(forced);
    expect(talk).not.toContain("identity");
  });
  it("reserves a decorative slot", () => {
    expect(renderToStaticMarkup(<AvatarSlot dashed />)).toContain('aria-hidden="true"');
  });
});

describe("TypeIcon / PriorityIcon", () => {
  it("names the type, or shows it as a visible label", () => {
    expect(renderToStaticMarkup(<TypeIcon type="bug" />)).toContain('aria-label="Bug"');
    const labelled = renderToStaticMarkup(<TypeIcon type="improvement" label />);
    expect(labelled).toContain(">Improvement</span>");
    expect(labelled).not.toContain("aria-label");
  });
  it("names high and low, and reserves a silent slot for normal", () => {
    expect(renderToStaticMarkup(<PriorityIcon priority="high" />)).toContain(
      'aria-label="High priority"',
    );
    expect(renderToStaticMarkup(<PriorityIcon priority="low" />)).toContain(
      'aria-label="Low priority"',
    );
    const normal = renderToStaticMarkup(<PriorityIcon priority="normal" />);
    expect(normal).toContain('aria-hidden="true"');
    expect(normal).not.toContain("<svg");
  });
});

describe("Age", () => {
  it("wraps each unit so it can sit a size smaller, and keeps unit-less text whole", () => {
    const html = renderToStaticMarkup(<Age text="3h 05m" />);
    expect(html).toMatch(/3<span class="[^"]*">h<\/span>/);
    expect(html).toMatch(/05<span class="[^"]*">m<\/span>/);
    expect(renderToStaticMarkup(<Age text="now" />)).toContain("<span>now</span>");
  });
});

describe("Tabs", () => {
  it("keeps one tab stop on the selected tab", () => {
    const html = renderToStaticMarkup(
      <Tabs
        label="Ticket"
        value="runs"
        onChange={() => undefined}
        items={[
          { id: "overview", label: "Overview" },
          { id: "runs", label: "Runs", count: 2 },
        ]}
      />,
    );
    expect(html).toContain('role="tablist"');
    expect(count(html, /tabindex="0"/g)).toBe(1);
    expect(html).toMatch(/aria-selected="true" tabindex="0">Runs/);
  });
});
