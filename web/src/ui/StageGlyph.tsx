import { roundText, type GateState, type GlyphModel, type StepState } from "../domain/stage";
import { cx } from "./cx";
import { fitGlyphSize, glyphItems, glyphTitle, isFinished, parkedGate } from "./glyph";
import s from "./StageGlyph.module.css";
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

export interface StageGlyphProps {
  model: GlyphModel;
  /** Rows: sm (default; drops to xs by itself when the pipeline doesn't fit the 81px slot) or xs.
   *  lg is the board size (no slot). */
  size?: "xs" | "sm" | "lg" | undefined;
  /** Show the label (the parked gate's short name, else the current stage). Default true. */
  label?: boolean | undefined;
  className?: string | undefined;
}

/**
 * The stage glyph (refresh HANDOFF §5): one segment per stage, a diamond per gate, in a fixed 81px
 * slot so the label beside it sits 90px into its cell. Its `title` gives the full path.
 */
export function StageGlyph({ model, size = "sm", label = true, className }: StageGlyphProps) {
  const parked = parkedGate(model);
  const failed = !parked && model.stages.some((st) => st.state === "failed");
  const finished = isFinished(model);
  const title = glyphTitle(model);
  const text = label ? model.label : "";
  const round = model.round;
  const named =
    model.label && round ? `${model.label}, ${roundText(round).toLowerCase()}` : model.label;
  const drawn = size === "sm" ? fitGlyphSize(model) : size;
  return (
    <span
      className={cx(
        s.stg,
        drawn === "xs" && s.xs,
        drawn === "lg" && s.lg,
        model.live && s.isLive,
        parked?.state === "you" && s.isGateYou,
        parked?.state === "waiting" && s.isGateWait,
        failed && s.isBlocked,
        finished && s.isFinished,
        className,
      )}
      role="img"
      aria-label={named ? `${named}: ${title}` : title}
      title={title}
    >
      <span className={s.segs}>
        {glyphItems(model.stages).map((it) =>
          it.type === "gate" ? (
            <u key={`g${String(it.index)}`} className={GATE_CLASS[it.gate.state]} />
          ) : (
            <i key={`s${String(it.index)}`} className={STEP_CLASS[it.stage.state]} />
          ),
        )}
      </span>
      {text ? (
        finished ? (
          <b>
            <TickBadge size={12} />
            {text}
          </b>
        ) : (
          <b>
            {text}
            {round ? (
              <span className={s.round} title={roundText(round)}>
                {roundText(round, true)}
              </span>
            ) : null}
          </b>
        )
      ) : null}
    </span>
  );
}
