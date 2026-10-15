// The Server / My client choice beside a dispatching button (the Ticket page header, the Pipeline
// tab's Run from…). Renders nothing unless the target rule offers the choice.
import { Select } from "../../editor";
import { isTarget, targetOptions } from "./target";
import type { DispatchTargetState } from "./useDispatchTarget";

export function TargetPicker({
  target,
  disabled,
  size,
}: {
  target: DispatchTargetState;
  disabled?: boolean | undefined;
  size?: "sm" | "md" | undefined;
}) {
  if (!target.offer) return null;
  return (
    <Select
      size={size}
      aria-label="Run on"
      value={target.value}
      options={targetOptions(target)}
      disabled={disabled}
      onChange={(v) => {
        if (isTarget(v)) target.pick(v);
      }}
    />
  );
}
