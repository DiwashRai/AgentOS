// The New ticket dialog's Workflow select: every workflow, the default first and preselected, the
// chosen one's description as the hint. Presentational: the dialog owns the value.
import { Field, Select } from "../../editor";
import { workflowHint, workflowOptions, type Workflow } from "./model";

export interface WorkflowPickerProps {
  workflows: readonly Workflow[];
  /** A workflow id as a string. */
  value: string;
  onChange: (value: string) => void;
  className?: string | undefined;
}

export function WorkflowPicker({ workflows, value, onChange, className }: WorkflowPickerProps) {
  return (
    <Field label="Workflow" hint={workflowHint(workflows, value)} className={className}>
      {({ id, describedBy }) => (
        <Select
          id={id}
          value={value}
          aria-describedby={describedBy}
          options={workflowOptions(workflows)}
          onChange={onChange}
        />
      )}
    </Field>
  );
}
