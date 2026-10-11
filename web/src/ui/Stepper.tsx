import type { GateState, StepState } from "../domain/stage";
import { cx } from "./cx";
import {
  collapsesGates,
  gateTitle,
  glyphItems,
  isBareGate,
  stageCaption,
  type StepperModel,
} from "./glyph";
import { YouPill } from "./StatusMark";
import s from "./Stepper.module.css";

const STEP_CLASS: Record<StepState, string | undefined> = {
  done: s.isDone,
  current: s.isCur,
  upcoming: undefined,
  skipped: s.isSkip,
  failed: s.isFailed,
  stale: s.isStale,
};

const GATE_CLASS: Record<GateState, string | undefined> = {
  pending: undefined,
  passed: s.isPassed,
  waiting: s.isWait,
  you: s.isYou,
};

export interface StepperProps {
  /** A GlyphModel; a stage may also carry `added` for the "ADDED" caption. */
  model: StepperModel;
  /** Peek / narrow: every inactive gate is a bare diamond. */
  compact?: boolean | undefined;
  className?: string | undefined;
}

export function Stepper({ model, compact = false, className }: StepperProps) {
  const collapse = collapsesGates(model, compact);
  const last = model.stages.length - 1;
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
          return (
            <li key={`g${String(it.index)}`} className={cls} title={title}>
              <u />
              <span className={s.nm}>{gate.short}</span>
              {gate.state === "you" ? (
                <YouPill className={s.you} />
              ) : gate.state === "waiting" && gate.person ? (
                <span className={s.st}>{gate.person}</span>
              ) : null}
            </li>
          );
        }
        const { stage } = it;
        const caption = stageCaption(stage, it.index === last, model.live);
        return (
          <li
            key={`s${String(it.index)}`}
            className={cx(s.s, STEP_CLASS[stage.state])}
            title={stage.name + (caption ? ` · ${caption.toLowerCase()}` : "")}
            aria-current={
              stage.state === "current" || stage.state === "failed" ? "step" : undefined
            }
          >
            <span className={s.bar} />
            <span className={s.nm}>{stage.name}</span>
            {caption ? <span className={s.st}>{caption}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
