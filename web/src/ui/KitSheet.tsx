// The kit sheet (mounted at /kit): every primitive in every state, in both themes side by side.
// The token selectors are plain attribute selectors, so each `data-theme` subtree themes itself.
// Data here is hand-written fixtures, not server data.
import { useState } from "react";
import { planView } from "../domain/plan";
import type { GlyphGate, GateState, StepState } from "../domain/stage";
import { Avatar, AvatarSlot } from "./Avatar";
import { Age } from "./Age";
import { Badge } from "./Badge";
import { Button, IconButton, litRowClass } from "./Button";
import { Checklist } from "./Checklist";
import { Chip } from "./Chip";
import { CountPill } from "./CountPill";
import { cx } from "./cx";
import { Dialog, DialogTitle } from "./Dialog";
import { Disclosure, Fold } from "./Disclosure";
import { EmptyRow } from "./EmptyRow";
import { EmptyState } from "./EmptyState";
import { Faces } from "./Faces";
import type { StepperModel } from "./glyph";
import { GroupHead } from "./GroupHead";
import { ChevronIcon, CloseIcon } from "./icons";
import { Kbd } from "./Kbd";
import { KeyChip } from "./KeyChip";
import s from "./KitSheet.module.css";
import { Inline, Spacer, Stack } from "./Layout";
import { Link, LinkButton } from "./Link";
import { RunMeter } from "./RunMeter";
import type { MeterState } from "./meter";
import { PriorityIcon } from "./PriorityIcon";
import { FramedList, Sheet } from "./Sheet";
import { StageGlyph } from "./StageGlyph";
import { StatusLine } from "./StatusLine";
import { StatusMark } from "./StatusMark";
import { Stepper } from "./Stepper";
import { Tabs } from "./Tabs";
import { Tag } from "./Tag";
import text from "./text.module.css";
import { TickBadge } from "./TickBadge";
import { TypeIcon } from "./TypeIcon";
import { ZoneHead } from "./ZoneHead";

const gate = (label: string, short: string, state: GateState, person?: string): GlyphGate => ({
  label,
  short,
  state,
  person,
});

const CASES: { title: string; model: StepperModel }[] = [
  {
    title: "Mid-run, live",
    model: {
      live: true,
      label: "Build",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Build", state: "current" },
        { name: "Verify", state: "upcoming" },
        { name: "Review", state: "upcoming", gate: gate("PR review", "PR review", "pending") },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Parked at a gate on you",
    model: {
      live: false,
      label: "Sign-off",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "you") },
        { name: "Build", state: "upcoming" },
        { name: "Verify", state: "upcoming" },
        { name: "Review", state: "upcoming", gate: gate("PR review", "PR review", "pending") },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Parked at a gate on someone, added stage",
    model: {
      live: false,
      label: "Sec review",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Build", state: "done" },
        { name: "Verify", state: "done" },
        { name: "Backfill", state: "done", added: true },
        {
          name: "Review",
          state: "done",
          gate: gate("Security review", "Sec review", "waiting", "Sam"),
        },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Skipped stage",
    model: {
      live: false,
      label: "Build",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done" },
        { name: "Build", state: "current" },
        { name: "Verify", state: "skipped" },
        { name: "Review", state: "upcoming" },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Failed stage",
    model: {
      live: false,
      label: "Build",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Build", state: "failed" },
        { name: "Verify", state: "upcoming" },
        { name: "Review", state: "upcoming" },
        { name: "Done", state: "upcoming" },
      ],
    },
  },

  {
    title: "Review loop, live in round 2 of 3",
    model: {
      live: true,
      label: "Review",
      round: { n: 2, of: 3 },
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Implement", state: "done", round: { n: 2, of: 3 } },
        { name: "Review", state: "current", round: { n: 2, of: 3 } },
        { name: "Open PR", state: "upcoming" },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Review loop, checks still failing",
    model: {
      live: false,
      label: "Review",
      round: { n: 3, of: 3 },
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Implement", state: "done", round: { n: 3, of: 3 } },
        { name: "Review", state: "failed", round: { n: 3, of: 3 } },
        { name: "Open PR", state: "upcoming" },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Three gates, parked on you",
    model: {
      live: false,
      label: "Check",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Build", state: "done", gate: gate("Build check", "Check", "you") },
        { name: "Verify", state: "upcoming" },
        { name: "Review", state: "upcoming", gate: gate("PR review", "PR review", "pending") },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Three gates, live",
    model: {
      live: true,
      label: "Verify",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Build", state: "done", gate: gate("Build check", "Check", "passed") },
        { name: "Verify", state: "current" },
        { name: "Backfill", state: "upcoming", added: true },
        { name: "Review", state: "upcoming", gate: gate("PR review", "PR review", "pending") },
        { name: "Done", state: "upcoming" },
      ],
    },
  },
  {
    title: "Done",
    model: {
      live: false,
      label: "Done",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
        { name: "Build", state: "done" },
        { name: "Verify", state: "done" },
        { name: "Review", state: "done", gate: gate("PR review", "PR review", "passed") },
        { name: "Done", state: "current" },
      ],
    },
  },
];

/** The refresh mock's pipeline: Inbox · Plan ◆ Sign-off · Implement · Lint · Review · Open PR · Done. */
function mockPipe(states: StepState[], signOff: GateState, live = false): StepperModel {
  const names = ["Inbox", "Plan", "Implement", "Lint", "Review", "Open PR", "Done"];
  const stages = names.map((name, i) => ({
    name,
    state: states[i] ?? "upcoming",
    gate: i === 1 ? gate("Plan sign-off", "Sign-off", signOff) : undefined,
  }));
  const current = stages.find((st) => st.state === "current" || st.state === "failed");
  return {
    live,
    label: signOff === "you" || signOff === "waiting" ? "Sign-off" : (current?.name ?? ""),
    stages,
  };
}

/** Row-glyph cases: every label should start 90px into its cell (the dashed line is the cell edge). */
const ROW_GLYPHS: { title: string; model: StepperModel }[] = [
  {
    title: "Parked on you (AOS-14)",
    model: mockPipe(["done", "done", "upcoming", "skipped"], "you"),
  },
  { title: "Inbox", model: mockPipe(["current"], "pending") },
  {
    title: "Running",
    model: mockPipe(["done", "done", "current", "skipped"], "passed", true),
  },
  {
    title: "Blocked",
    model: mockPipe(["done", "done", "failed", "skipped"], "passed"),
  },
  {
    title: "Done",
    model: mockPipe(["done", "done", "done", "skipped", "done", "done", "current"], "passed"),
  },
  {
    title: "Review loop, round 2 of 3",
    model: {
      ...mockPipe(["done", "done", "done", "skipped", "current"], "passed", true),
      round: { n: 2, of: 3 },
    },
  },
  {
    title: "Long pipeline (auto xs)",
    model: {
      live: false,
      label: "Fuzz",
      stages: [
        "Inbox",
        "Plan",
        "Spec",
        "Build",
        "Fuzz",
        "Lint",
        "Test",
        "Review",
        "Open PR",
        "Done",
      ].map((name, i): StepperModel["stages"][number] => ({
        name,
        state: i < 4 ? "done" : i === 4 ? "current" : "upcoming",
        gate: i === 1 ? gate("Plan sign-off", "Sign-off", "passed") : undefined,
      })),
    },
  },
];

const STEPS = [
  "Check out branch",
  "Implement change",
  "Update tests",
  "Run test suite",
  "Open draft PR",
];
const METERS: { state: MeterState; k: number }[] = [
  { state: "running", k: 3 },
  { state: "finished", k: 5 },
  { state: "queued", k: 0 },
];

const PEOPLE = [
  { id: 1, name: "Diwash Rai" },
  { id: 2, name: "Maya Chen" },
  { id: 3, name: "Tom Reid" },
  { id: 4, name: "Aisha Khan" },
  { id: 5, name: "Sam Okoro" },
];

type TicketTab = "overview" | "runs" | "context" | "pipeline" | "activity";

function Panel({ theme, title }: { theme: "light" | "dark"; title: string }) {
  const [collapsed, setCollapsed] = useState(false);
  const [tab, setTab] = useState<TicketTab>("overview");
  const id = (x: string) => `${theme}-${x}`;
  return (
    <div className={s.panel} data-theme={theme}>
      <h1>{title}</h1>

      <section className={s.section} aria-labelledby={id("marks")}>
        <ZoneHead id={id("marks")} title="Status marks" note="one form per state" />
        <div className={s.card}>
          <div className={s.stack}>
            <StatusMark kind="run" name="Builder · run #42" text="Implement change" />
            <StatusMark kind="queued" name="Queued" text="Next for a runner" />
            <StatusMark kind="you" ask="Sign off the plan" text="Plan v2 is ready" />
            <StatusMark kind="wait" name="Sam" ask="Security review" text="PR #412" />
            <StatusMark kind="blk" name="Blocked" text="Tests failed on main" />
            <StatusMark kind="done" name="Merged" text="#412 · just now" />
            <StatusMark kind="idle" name="Inbox" text="Nobody on it yet" />
          </div>
          <div className={s.row}>
            <span className={s.cap}>Glyph 8</span>
            {(["run", "wait", "you", "blk", "done", "queued", "idle"] as const).map((k) => (
              <StatusMark key={k} kind={k} glyphOnly />
            ))}
            <span className={s.cap}>Glyph 7</span>
            {(["run", "wait", "you", "blk", "done", "queued", "idle"] as const).map((k) => (
              <StatusMark key={k} kind={k} glyphOnly size={7} />
            ))}
          </div>
        </div>
      </section>

      <section className={s.section} aria-labelledby={id("glyph")}>
        <ZoneHead id={id("glyph")} title="Stage glyph" note="81px slot · label at 90px" />
        <div className={s.card}>
          <div className={s.glyphs}>
            {ROW_GLYPHS.map((c) => (
              <div key={c.title} className={s.glyphRow}>
                <span className={s.cap}>{c.title}</span>
                <span className={s.slotCell}>
                  <StageGlyph model={c.model} />
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className={s.card}>
          <span className={s.cap}>xs · sm · lg · no label</span>
          <div className={s.glyphs}>
            {CASES.map((c) => (
              <GlyphRow key={c.title} title={c.title} model={c.model} />
            ))}
          </div>
        </div>
      </section>

      <section className={s.section} aria-labelledby={id("stepper")}>
        <ZoneHead id={id("stepper")} title="Stepper" note="full, then compact (peek)" />
        <div className={s.card}>
          {ROW_GLYPHS.slice(0, 5).map((c) => (
            <div key={`row-${c.title}`} className={s.stepperCase}>
              <span className={s.cap}>{c.title}</span>
              <Stepper model={c.model} />
            </div>
          ))}
          {CASES.map((c) => (
            <div key={c.title} className={s.stepperCase}>
              <span className={s.cap}>{c.title}</span>
              <Stepper model={c.model} />
              <div className={s.peekWidth}>
                <Stepper model={c.model} compact />
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className={s.section} aria-labelledby={id("meter")}>
        <ZoneHead id={id("meter")} title="Run meter" note="tile · row 56 · row 36 · hair · strip" />
        <div className={s.card}>
          <div className={s.meters}>
            {METERS.map((m) => (
              <MeterRow key={m.state} state={m.state} k={m.k} />
            ))}
          </div>
          {METERS.map((m) => (
            <RunMeter
              key={m.state}
              k={m.k}
              n={STEPS.length}
              state={m.state}
              size="strip"
              labels={STEPS}
            />
          ))}
          <RunMeter k={2} n={4} state="running" size="strip" labels={STEP_PATHS} />
        </div>
      </section>

      <section className={s.section} aria-labelledby={id("buttons")}>
        <ZoneHead id={id("buttons")} title="Buttons" />
        <div className={s.card}>
          {(["xs", "sm", "md", "lg"] as const).map((size) => (
            <div key={size} className={s.row}>
              <span className={s.cap}>{size}</span>
              <Button size={size}>New ticket</Button>
              <Button size={size} variant="quiet">
                Request changes
              </Button>
              <Button size={size} variant="you">
                Approve
              </Button>
              <Button size={size} variant="ink">
                Retry
              </Button>
              <Button size={size} variant="accent">
                Dispatch run
              </Button>
              <Button size={size} variant="outline">
                {size === "xs" ? "Add context" : "Take in"}
              </Button>
              <Button size={size} variant="danger">
                Abandon
              </Button>
              <Button size={size} disabled>
                Disabled
              </Button>
            </div>
          ))}
          <span className={s.cap}>
            Take in lights up with its row (hover, or Tab to the button)
          </span>
          <div className={cx(s.demoRow, litRowClass)}>
            <KeyChip>SCRUM-23</KeyChip>
            <TypeIcon type="feature" label />
            <span>Export a ticket&apos;s runs as CSV</span>
            <Button size="sm" variant="outline" litOnRow>
              Take in
            </Button>
          </div>
          <div className={s.row}>
            <span className={s.cap}>Icon</span>
            <IconButton label="Close">
              <CloseIcon />
            </IconButton>
            <IconButton label="Close" bare>
              <CloseIcon />
            </IconButton>
            <IconButton label="More">
              <ChevronIcon />
            </IconButton>
            <IconButton label="Disabled" disabled>
              <CloseIcon />
            </IconButton>
          </div>
        </div>
      </section>

      <section className={s.section} aria-labelledby={id("bits")}>
        <ZoneHead id={id("bits")} title="Tags, people, keys, chips, links" />
        <div className={s.card}>
          <div className={s.row}>
            <KeyChip size="lg">AOS-14</KeyChip>
            <Tag>
              <TypeIcon type="feature" />
              Feature
            </Tag>
            <Tag>
              <PriorityIcon priority="high" />
              High
            </Tag>
            <Tag quiet>web-portal</Tag>
            <Tag>Jira PLAT-2231</Tag>
            <Tag prio>High</Tag>
          </div>
          <div className={s.row}>
            <span className={s.cap}>Key chips</span>
            {["AOS-14", "SCRUM-21", "PLAT-3", "WEB-1", "OPS-2", "DATA-9"].map((k) => (
              <KeyChip key={k}>{k}</KeyChip>
            ))}
            <KeyChip size="sm">SCRUM</KeyChip>
          </div>
          <div className={s.row}>
            <span className={s.cap}>Type, priority</span>
            {(["feature", "bug", "improvement", "task"] as const).map((t) => (
              <TypeIcon key={t} type={t} />
            ))}
            {(["feature", "bug", "improvement", "task"] as const).map((t) => (
              <TypeIcon key={`l-${t}`} type={t} label />
            ))}
            {(["high", "normal", "low"] as const).map((p) => (
              <span key={p} className={s.row}>
                <PriorityIcon priority={p} />
                <span>{p} slot</span>
              </span>
            ))}
          </div>
          <div className={s.row}>
            <span className={s.cap}>Counts, ticks</span>
            <CountPill count={3} />
            <CountPill count={1} tone="you" />
            <CountPill count={12} tone="done" />
            <TickBadge />
            <TickBadge size={14} label="Done" />
            <Age text="3h 05m" />
            <Age text="38m" size="lg" />
            <ZoneHead
              title="Done recently"
              tone="done"
              count={3}
              right={
                <>
                  <TickBadge />
                  <span>
                    <b>2 shipped</b> this week
                  </span>
                </>
              }
            />
          </div>
          <EmptyRow lead="All quiet">nothing running, nothing queued</EmptyRow>
          <div className={s.row}>
            <span className={s.cap}>Rows</span>
            {PEOPLE.map((p) => (
              <Avatar key={p.id} name={p.name} id={p.id} />
            ))}
            <AvatarSlot />
            <AvatarSlot dashed />
            <Avatar name="Tom Reid" id={3} size={30} />
            <Faces people={PEOPLE} />
          </div>
          <div className={s.row}>
            <span className={s.cap}>Talk</span>
            {PEOPLE.slice(0, 4).map((p) => (
              <Avatar key={p.id} name={p.name} id={p.id} palette="talk" />
            ))}
            <Avatar name="Diwash Rai" id={1} size={24} palette="talk" />
            <Avatar name="Maya Chen" id={2} size={26} />
            <Faces people={PEOPLE.slice(0, 2)} size={24} palette="talk" />
          </div>
          <div className={s.row}>
            <Kbd>/</Kbd>
            <Kbd>J</Kbd>
            <Kbd>K</Kbd>
            <Kbd>Esc</Kbd>
            <Chip k="md">TASKS.md</Chip>
            <Chip>SKILL.md</Chip>
            <Chip add>+ Add context</Chip>
            <Link href="#kit">Open ticket →</Link>
            <LinkButton>Show all</LinkButton>
          </div>
        </div>
      </section>

      <section className={s.section} aria-labelledby={id("text")}>
        <ZoneHead id={id("text")} title="Text and status lines" />
        <div className={s.card}>
          <h3 className={cx(text.heading, s.flush)}>Section heading</h3>
          <div className={s.row}>
            <span className={text.label}>Label</span>
            <span className={text.meta}>Meta · saved 3 min ago by Maya</span>
            <span className={text.error}>Required.</span>
            <span className={cx(text.meta, text.ellipsis, s.narrow)}>
              An ellipsis cuts a long line to one row
            </span>
          </div>
          <StatusLine>Loading Space settings</StatusLine>
          <StatusLine error>Can&apos;t load Space settings · 502</StatusLine>
          <StatusLine inset>Loading</StatusLine>
        </div>
      </section>

      <LayoutKit id={id} />

      <section className={s.section} aria-labelledby={id("heads")}>
        <ZoneHead id={id("heads")} title="Zone head, group head, tabs" />
        <div className={s.recess}>
          <div className={s.stack}>
            <ZoneHead
              title="Needs you"
              tone="you"
              count={1}
              note="Longest wait first"
              right={<LinkButton>Show all</LinkButton>}
            />
            <ZoneHead title="Agents at work" count={5} note="running and queued" />
            <ZoneHead title="Waiting on others" />
            <GroupHead
              name="Needs you"
              count={1}
              glyph="you"
              summary={
                <>
                  Longest wait <b>38m</b>
                </>
              }
              collapsed={collapsed}
              onToggle={() => {
                setCollapsed((v) => !v);
              }}
            />
            <GroupHead name="Blocked" count={0} glyph="blk" collapsed onToggle={noop} />
            <GroupHead
              name="Inbox"
              count={3}
              glyph="idle"
              summary="→ Add context, then dispatch"
              collapsed={false}
              onToggle={noop}
            />
            <GroupHead
              name="Done recently"
              count={12}
              glyph="done"
              summary={
                <>
                  <b>2 shipped</b> this week
                </>
              }
              collapsed={false}
              onToggle={noop}
            />
            <GroupHead
              name="To do"
              count={3}
              icon={<KeyChip size="sm">SCRUM</KeyChip>}
              summary={
                <span className={s.jql}>
                  project = SCRUM AND statusCategory = &quot;To Do&quot;
                </span>
              }
              collapsed={false}
              onToggle={noop}
            />
            <EmptyRow lead="All quiet">nothing running, nothing queued</EmptyRow>
          </div>
        </div>
        <div className={s.card}>
          <Tabs<TicketTab>
            label="Ticket"
            value={tab}
            onChange={setTab}
            items={[
              { id: "overview", label: "Overview" },
              { id: "runs", label: "Runs", count: 4 },
              { id: "context", label: "Context", count: 3 },
              { id: "pipeline", label: "Pipeline" },
              { id: "activity", label: "Activity" },
            ]}
            right={
              <>
                <span className={s.cap}>Synced from Jira</span>
                <Kbd>Q</Kbd>
              </>
            }
          />
        </div>
      </section>
    </div>
  );
}

const STEP_PATHS = [
  "Read web/src/domain/stage.ts",
  "Port kit.css to ui/StageGlyph.module.css",
  "Run npx_vitest_run",
  "Open draft PR",
];

function noop() {
  /* static demo head */
}

function GlyphRow({ title, model }: { title: string; model: StepperModel }) {
  return (
    <div className={s.glyphRow}>
      <span className={s.cap}>{title}</span>
      <StageGlyph model={model} size="xs" />
      <StageGlyph model={model} />
      <StageGlyph model={model} size="lg" />
      <StageGlyph model={model} label={false} />
    </div>
  );
}

function MeterRow({ state, k }: { state: MeterState; k: number }) {
  return (
    <>
      <span className={s.cap}>{state}</span>
      <div className={s.stack}>
        <RunMeter k={k} n={STEPS.length} state={state} size="tile" />
        <RunMeter k={k} n={STEPS.length} state={state} size="hair" />
      </div>
      <RunMeter k={k} n={STEPS.length} state={state} size="row" />
      <RunMeter k={k} n={STEPS.length} state={state} size="row" width={36} />
    </>
  );
}

const PLAN = [
  { text: "1. Baseline the CSS line counts", done: true, section: "Measure" },
  { text: "2. One vocabulary and one `duration` formatter", done: true, section: "Domain" },
  { text: "3. The ticket view-model", done: true, section: "Domain" },
  { text: "4. Layout and surface primitives", done: false, section: "Kit" },
  { text: "5. **SegmentedControl** and DataList", done: false, section: "Kit" },
  { text: "6. Dialog and useDocumentKey", done: false, section: "Kit" },
  { text: "7. Peek and New ticket on Dialog", done: false, section: "Kit" },
  { text: "8. Document keys on the hook", done: false, section: "Kit" },
];
const FLAT_PLAN = PLAN.map(({ text: t, done }) => ({ text: t, done }));

/** Stack / Inline, Sheet / FramedList, Badge, EmptyState, Disclosure / Fold and Checklist. */
function LayoutKit({ id }: { id: (x: string) => string }) {
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(false);
  return (
    <section className={s.section} aria-labelledby={id("layout")}>
      <ZoneHead id={id("layout")} title="Layout, surfaces, badges, empty states, folds" />

      <div className={s.card}>
        <span className={s.cap}>Stack gap 4 · Inline gap 7, rowGap 3, wrap · Spacer</span>
        <Stack gap={4}>
          <Inline gap={7} rowGap={3} wrap>
            <Tag>One</Tag>
            <Tag>Two</Tag>
            <Tag>Three</Tag>
          </Inline>
          <Inline gap={4}>
            <Kbd>J</Kbd>
            <Kbd>K</Kbd>
            <span className={text.meta}>move</span>
            <Spacer />
            <span className={text.meta}>right-hand summary</span>
          </Inline>
        </Stack>
      </div>

      <div className={s.recess}>
        <Stack gap={5}>
          <Sheet pad="md">
            <span className={s.cap}>Sheet · edge, md</span>
          </Sheet>
          <Sheet pad="sm" elevation="float">
            <span className={s.cap}>Sheet · float, sm</span>
          </Sheet>
          <FramedList aria-label="Framed list">
            {["web-portal", "agentos", "infra"].map((r) => (
              <li key={r} className={s.listRow}>
                {r}
              </li>
            ))}
          </FramedList>
          <FramedList framed={false} rules="under" topRule aria-label="Ruled list">
            {["Build · run #42", "Plan · run #41"].map((r) => (
              <li key={r} className={s.listRow}>
                {r}
              </li>
            ))}
          </FramedList>
        </Stack>
      </div>

      <div className={s.card}>
        <div className={s.row}>
          <span className={s.cap}>Count</span>
          <Badge tone="count" count={3} />
          <Badge tone="count" count={0} />
          <Badge tone="count" count={1} you />
          <span className={s.cap}>Status</span>
          <Badge tone="status" title="Jira status: In Progress">
            In Progress
          </Badge>
          <Badge tone="status">To Do</Badge>
        </div>
        <div className={s.row}>
          <span className={s.cap}>Counter</span>
          <Badge tone="counter" count={2} you mark={<StatusMark kind="you" glyphOnly size={7} />}>
            need you
          </Badge>
          <Badge tone="counter" count={5} mark={<StatusMark kind="run" glyphOnly size={7} />}>
            running
          </Badge>
          <Badge tone="counter" count={0} you mark={<StatusMark kind="you" glyphOnly size={7} />}>
            need you
          </Badge>
          <Badge tone="counter" count={0}>
            to take in
          </Badge>
        </div>
        <div className={s.row}>
          <span className={s.cap}>Verdict, words, stat, tag</span>
          <Badge tone="pass">Pass</Badge>
          <Badge tone="fail">Fail</Badge>
          <Badge tone="ok">Passed</Badge>
          <Badge tone="bad">Failed</Badge>
          <Badge tone="stat">
            <b>2 shipped</b> this week
          </Badge>
          <Badge>Jira PLAT-2231</Badge>
          <Badge quiet>web-portal</Badge>
          <Badge prio>High</Badge>
        </div>
      </div>

      <div className={s.card}>
        <EmptyState lead="All clear">nothing needs you</EmptyState>
        <EmptyState lead="All quiet" action={<LinkButton>Show done</LinkButton>}>
          nothing running, nothing queued
        </EmptyState>
        <EmptyState form="line" action={<Button size="sm">New ticket</Button>}>
          Nothing here yet. Create a ticket.
        </EmptyState>
        <EmptyState form="inset">
          No intake queries yet. An admin adds them in <Link href="#kit">Settings › Intake</Link>.
        </EmptyState>
        <EmptyState form="note">No plan yet.</EmptyState>
      </div>

      <div className={s.card}>
        <div className={s.row}>
          <Disclosure
            expanded={open}
            onToggle={() => {
              setOpen((v) => !v);
            }}
            controls={id("disclosed")}
            meta="3 files"
          >
            Context files
          </Disclosure>
        </div>
        {open ? (
          <p id={id("disclosed")} className={text.meta}>
            TASKS.md · SKILL.md · learnings.log
          </p>
        ) : null}
        <Fold summary="Check notes" meta="2 repairs">
          <p className={text.meta}>The notes and repairs fold under the check.</p>
        </Fold>
        <Fold summary="How each stage was called" size="section">
          <p className={text.meta}>The run&apos;s manifest.</p>
        </Fold>
      </div>

      <div className={s.card}>
        <Checklist plan={planView(PLAN)} title="Plan" id={id("plan")} />
        <Checklist
          plan={null}
          title="Plan"
          id={id("plan-none")}
          empty={<EmptyState form="note">No plan yet.</EmptyState>}
        />
      </div>
      <div className={cx(s.card, s.peekWidth)}>
        <Checklist
          plan={planView(FLAT_PLAN, 6)}
          density="compact"
          title="Plan"
          level={3}
          id={id("plan-compact")}
        />
        <span className={s.cap}>Compact, headless (the label is the view&apos;s)</span>
        <Checklist plan={planView(FLAT_PLAN.slice(0, 3))} density="compact" label="Plan" />
      </div>

      <div className={s.card}>
        <Button
          size="sm"
          onClick={() => {
            setDialog(true);
          }}
        >
          Open dialog
        </Button>
        {dialog ? (
          <Dialog
            onClose={() => {
              setDialog(false);
            }}
          >
            {(close) => (
              <Stack gap={6} className={s.dialogBody}>
                <DialogTitle>A dialog</DialogTitle>
                <p className={text.meta}>Tab stays inside; Escape or the scrim closes it.</p>
                <Inline gap={4}>
                  <Spacer />
                  <Button size="sm" onClick={close} data-autofocus>
                    Close
                  </Button>
                </Inline>
              </Stack>
            )}
          </Dialog>
        ) : null}
      </div>
    </section>
  );
}

export default function KitSheet() {
  return (
    <div className={s.sheet}>
      <Panel theme="light" title="Type Case Cool · light" />
      <Panel theme="dark" title="Harbour · dark" />
    </div>
  );
}
