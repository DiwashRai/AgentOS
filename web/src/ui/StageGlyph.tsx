import type { GateState, GlyphModel, StepState } from "../domain/stage";
import { cx } from "./cx";
import { glyphItems, glyphTitle, parkedGate } from "./glyph";
import s from "./StageGlyph.module.css";

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

export interface StageGlyphProps {
  model: GlyphModel;
  size?: "xs" | "sm" | "lg" | undefined;
  /** Show the mono label (the parked gate's short name, else the current stage). Default true. */
  label?: boolean | undefined;
  className?: string | undefined;
}

export function StageGlyph({ model, size = "sm", label = true, className }: StageGlyphProps) {
  const parked = parkedGate(model);
  const failed = !parked && model.stages.some((st) => st.state === "failed");
  const title = glyphTitle(model);
  const text = label ? model.label : "";
  return (
    <span
      className={cx(
        s.stg,
        size === "xs" && s.xs,
        size === "lg" && s.lg,
        model.live && s.isLive,
        parked?.state === "you" && s.isGateYou,
        parked?.state === "waiting" && s.isGateWait,
        failed && s.isBlocked,
        className,
      )}
      role="img"
      aria-label={model.label ? `${model.label}: ${title}` : title}
      title={title}
    >
      {glyphItems(model.stages).map((it) =>
        it.type === "gate" ? (
          <u key={`g${String(it.index)}`} className={GATE_CLASS[it.gate.state]} />
        ) : (
          <i key={`s${String(it.index)}`} className={STEP_CLASS[it.stage.state]} />
        ),
      )}
      {text ? <b>{text}</b> : null}
    </span>
  );
}
