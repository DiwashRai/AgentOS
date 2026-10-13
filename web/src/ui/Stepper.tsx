import type { GateState, StepState } from "../domain/stage";
import { cx } from "./cx";
import {
  collapsesGates,
  gateTitle,
  glyphItems,
  isAhead,
  isBareGate,
  isFinished,
  roundCaption,
  stageCaption,
  YOUR_MOVE,
  type StepperModel,
} from "./glyph";
import s from "./Stepper.module.css";
import { TickBadge } from "./TickBadge";

const STEP_CLASS: Record<StepState, string | undefined> = {
  done: s.isDone,
  current: s.isCur,
  upcoming: undefined,
  skipped: s.isSkip,
  failed: s.isFailed,
};

const GATE_CLASS: Record<GateState, string | undefined> = {
  pending: undefined,
  passed: s.isPassed,
  waiting: s.isWait,
  you: s.isYou,
};

export interface StepperProps {
  /** A GlyphModel; a stage may also carry `added` for the "Added" caption. */
  model: StepperModel;
  /** Peek / narrow: every inactive gate is a bare diamond. */
  compact?: boolean | undefined;
  className?: string | undefined;
}

/**
 * The labelled stepper (refresh HANDOFF §5 "Stepper"): 3px bars (skipped 1.5px) on one 10px track,
 * so every name shares one baseline. A gate on you reads "Your move"; a finished ticket's last stage
 * gets the tick badge and "Complete".
 */
export function Stepper({ model, compact = false, className }: StepperProps) {
  const collapse = collapsesGates(model, compact);
  const last = model.stages.length - 1;
  const finished = isFinished(model);
  return (
    <ol
      className={cx(s.stepper, model.live && s.isLive, collapse && s.isCompact, className)}
      aria-label="Pipeline"
    >
      {glyphItems(model.stages).map((it) => {
        if (it.type === "gate") {
          const { gate } = it;
          const bare = isBareGate(gate, collapse);
          const cls = cx(s.g, GATE_CLASS[gate.state], bare && s.isBare);
          const title = gateTitle(gate, bare);
          if (bare) {
            return (
              <li key={`g${String(it.index)}`} className={cls} title={title} aria-label={title}>
                <u />
              </li>
            );
          }
          const caption =
            gate.state === "you"
              ? YOUR_MOVE
              : gate.state === "waiting" && gate.person
                ? gate.person
                : "";
          return (
            <li key={`g${String(it.index)}`} className={cls} title={title}>
              <u />
              <span className={s.nm}>{gate.short}</span>
              {caption ? <span className={s.st}>{caption}</span> : null}
            </li>
          );
        }
        const { stage } = it;
        const isLast = it.index === last;
        const final = isLast && finished;
        const caption = stageCaption(stage, isLast, model.live, finished, model.closed);
        const round = roundCaption(stage);
        return (
          <li
            key={`s${String(it.index)}`}
            className={cx(
              s.s,
              STEP_CLASS[stage.state],
              stage.state === "skipped" && isAhead(model, it.index) && s.isAhead,
              final && s.isFinal,
            )}
            title={[stage.name, caption.toLowerCase(), round.toLowerCase()]
              .filter(Boolean)
              .join(" · ")}
            aria-current={
              stage.state === "current" || stage.state === "failed" ? "step" : undefined
            }
          >
            <span className={s.bar} />
            <span className={s.nm}>
              {final ? <TickBadge size={14} /> : null}
              {stage.name}
            </span>
            {caption ? <span className={s.st}>{caption}</span> : null}
            {round ? <span className={cx(s.st, s.round)}>{round}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
