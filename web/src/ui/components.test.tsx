// Markup checks via server rendering (node environment; no DOM library needed).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import type { StepperModel } from "./glyph";
import { RunMeter } from "./RunMeter";
import { StageGlyph } from "./StageGlyph";
import { StatusMark } from "./StatusMark";
import { Stepper } from "./Stepper";
import { Tabs } from "./Tabs";

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
  it("always renders the YOU pill for kind you, ignoring name", () => {
    const html = renderToStaticMarkup(<StatusMark kind="you" name="ignored" ask="Sign off" />);
    expect(html).toContain(">YOU</b>");
    expect(html).not.toContain("ignored");
    expect(html).toContain("· Sign off");
  });
  it("names a glyph-only mark for assistive tech", () => {
    const html = renderToStaticMarkup(<StatusMark kind="blk" glyphOnly />);
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="Blocked"');
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
});

describe("Button", () => {
  it("defaults to type=button and gives accent the arrow", () => {
    const html = renderToStaticMarkup(<Button variant="accent">Dispatch run</Button>);
    expect(html).toContain('type="button"');
    expect(html).toContain("<svg");
  });
});

describe("Avatar", () => {
  it("shows initials and names the person", () => {
    const html = renderToStaticMarkup(<Avatar name="Diwash Rai" id={1} />);
    expect(html).toContain(">DR</span>");
    expect(html).toContain('title="Diwash Rai"');
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
