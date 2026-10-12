// An age with tabular numbers and each unit a size smaller (refresh HANDOFF §5 "Age"): "38m",
// "17h 30m", "now".
import { Fragment } from "react";

import s from "./Compact.module.css";
import { ageParts } from "./model";

export function Age({ text }: { text: string }) {
  return (
    <span className={s.age}>
      {ageParts(text).map((p, i) => (
        <Fragment key={i}>
          {i > 0 ? " " : null}
          {p.n}
          {p.unit ? <span className={s.unit}>{p.unit}</span> : null}
        </Fragment>
      ))}
    </span>
  );
}
