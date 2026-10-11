// The kit sheet (mounted at /kit): every primitive in every state, in both themes side by side.
// The token selectors are plain attribute selectors, so each `data-theme` subtree themes itself.
// Data here is hand-written fixtures, not server data.
import { useState } from "react";
import type { GlyphGate, GateState } from "../domain/stage";
import { Avatar } from "./Avatar";
import { Button, IconButton } from "./Button";
import { Chip } from "./Chip";
import { Faces } from "./Faces";
import type { StepperModel } from "./glyph";
import { GroupHead } from "./GroupHead";
import { ChevronIcon, CloseIcon } from "./icons";
import { Kbd } from "./Kbd";
import s from "./KitSheet.module.css";
import { Link, LinkButton } from "./Link";
import { RunMeter } from "./RunMeter";
import type { MeterState } from "./meter";
import { StageGlyph } from "./StageGlyph";
import { StatusMark } from "./StatusMark";
import { Stepper } from "./Stepper";
import { Tabs } from "./Tabs";
import { Tag } from "./Tag";
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
    title: "Stale stages",
    model: {
      live: true,
      label: "Plan",
      stages: [
        { name: "Inbox", state: "done" },
        { name: "Plan", state: "current" },
        { name: "Build", state: "stale" },
        { name: "Verify", state: "stale" },
        { name: "Review", state: "upcoming" },
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
        <ZoneHead id={id("glyph")} title="Stage glyph" note="xs · sm · lg · no label" />
        <div className={s.card}>
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
          {(["sm", "md", "lg"] as const).map((size) => (
            <div key={size} className={s.row}>
              <span className={s.cap}>{size}</span>
              <Button size={size}>Request changes</Button>
              <Button size={size} variant="ink">
                Approve plan
              </Button>
              <Button size={size} variant="accent">
                Dispatch run
              </Button>
              <Button size={size} variant="quiet">
                Dispatch run
              </Button>
              <Button size={size} disabled>
                Disabled
              </Button>
            </div>
          ))}
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
            <Tag>Jira PLAT-2231</Tag>
            <Tag>Bug</Tag>
            <Tag prio>High</Tag>
            <Tag>agentos</Tag>
          </div>
          <div className={s.row}>
            {PEOPLE.slice(0, 4).map((p) => (
              <Avatar key={p.id} name={p.name} id={p.id} />
            ))}
            <Avatar name="Diwash Rai" id={1} size={24} />
            <Avatar name="Maya Chen" id={2} size={26} />
            <Avatar name="Tom Reid" id={3} size={30} />
            <Avatar name="kofi" id={7} />
            <Faces people={PEOPLE} />
            <Faces people={PEOPLE.slice(0, 2)} size={24} />
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

      <section className={s.section} aria-labelledby={id("heads")}>
        <ZoneHead id={id("heads")} title="Zone head, group head, tabs" />
        <div className={s.recess}>
          <div className={s.stack}>
            <ZoneHead
              title="Needs you"
              count={3}
              mark={<StatusMark kind="you" glyphOnly />}
              note="2 gates, 1 triage"
              right={<LinkButton>Show all</LinkButton>}
            />
            <ZoneHead title="Agents at work" count={5} note="running and queued" />
            <ZoneHead title="Waiting on others" />
            <GroupHead
              name="Needs you"
              count={3}
              glyph="you"
              summary={
                <>
                  <b>2</b> at a gate · <b>1</b> triage
                </>
              }
              collapsed={collapsed}
              onToggle={() => {
                setCollapsed((v) => !v);
              }}
            />
            <GroupHead name="Blocked" count={1} glyph="blk" collapsed onToggle={noop} />
            <GroupHead
              name="Done recently"
              count={12}
              glyph="done"
              collapsed={false}
              onToggle={noop}
            />
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

export default function KitSheet() {
  return (
    <div className={s.sheet}>
      <Panel theme="light" title="Type Case Cool · light" />
      <Panel theme="dark" title="Harbour · dark" />
    </div>
  );
}
