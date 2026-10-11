// The one recessed zone per view (kit .ao-surface). Views add their own layout (padding, grid)
// through `className`; items rest on it as sheets.
import type { ComponentPropsWithoutRef } from "react";

import s from "./Surface.module.css";

export function Surface({ className, ...rest }: ComponentPropsWithoutRef<"div">) {
  return <div className={[s.surface, className].filter(Boolean).join(" ")} {...rest} />;
}
